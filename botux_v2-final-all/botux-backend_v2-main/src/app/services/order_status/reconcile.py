from __future__ import annotations

from loguru import logger
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from runtime.logging import format_log_fields
from db.models import ExecutionRecord, OrderRecord, TradeOutcomeRecord
from db.repositories.executions_repo import ExecutionsRepository
from db.repositories.orders_repo import OrdersRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from app.services.signals.ownership import build_signal_ownership
from db.uow import UnitOfWork
from domain.enums import ExecutionStatus, OrderAction, OrderStatus, SignalStatus
from domain.models.execution_result import ExecutionResult
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from infra.brokers.router import BrokerRouter


pipeline_logger = logger.bind(pipeline_module=__name__)

@dataclass(frozen=True)
class _ActiveExecution:
    order_id: int
    broker_name: str | None
    broker_order_id: str | None
    current_status: str
    current_filled_qty: float
    current_avg_price: float | None


@dataclass(frozen=True)
class _BrokerOrder:
    broker_name: str
    broker_order_id: str
    symbol: str
    action: OrderAction
    quantity: float
    filled_qty: float
    avg_price: float | None
    status: str
    order_type: str
    market: str
    timestamp: datetime


class OrderStatusReconcileService:
    def __init__(self, broker_router: BrokerRouter) -> None:
        self._broker_router = broker_router

    async def reconcile_active_orders(self, *, limit: int = 100) -> dict[str, object]:
        await self._sanitize_sync_bot_ids()
        await self._repair_sync_trade_identity(limit=max(limit * 50, 5000))
        active = await self._load_active_executions(limit=limit)
        checked = 0
        updated = 0
        filled = 0
        failed = 0
        skipped = 0
        for item in active:
            if not item.broker_name or not item.broker_order_id:
                skipped += 1
                continue
            broker = self._broker_router.get(item.broker_name)
            if broker is None:
                skipped += 1
                pipeline_logger.log("WARNING", "pipeline.{} {}", "broker.reconcile_skipped", format_log_fields({"order_id": item.order_id, "broker_name": item.broker_name, "reason": "broker_not_found"}))
                continue
            checked += 1
            payload = await broker.get_order_status(item.broker_order_id)
            status = _parse_execution_status(payload.get("status"))
            filled_qty = _pick_float(payload, "filled_qty", "qty", "quantity")
            avg_price = _pick_optional_float(payload, "avg_price", "filled_avg_price", "price")
            pipeline_logger.log("INFO", "pipeline.{} {}", "broker.status_reconciled", format_log_fields({"order_id": item.order_id, "broker_name": item.broker_name, "broker_order_id": item.broker_order_id, "previous_status": item.current_status, "status": payload.get("status"), "filled_qty": filled_qty, "avg_price": avg_price}))
            if (
                status.value == item.current_status
                and filled_qty == item.current_filled_qty
                and avg_price == item.current_avg_price
            ):
                continue
            updated += 1
            execution = ExecutionResult(
                order_id=str(item.order_id),
                status=status,
                broker_order_id=item.broker_order_id,
                filled_qty=filled_qty,
                avg_price=avg_price,
            )
            async with UnitOfWork() as uow:
                executions_repo = ExecutionsRepository(connection=uow.connection)
                outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
                await executions_repo.save_execution(execution)
                if status in {ExecutionStatus.FILLED, ExecutionStatus.EXECUTED}:
                    await outcomes_repo.record_execution_entry(execution)
                    filled += 1
                elif status in {
                    ExecutionStatus.FAILED,
                    ExecutionStatus.REJECTED,
                    ExecutionStatus.CANCELED,
                    ExecutionStatus.EXPIRED,
                }:
                    failed += 1
        backfill = await self._backfill_recent_orders(limit=limit)
        return {
            "checked": checked,
            "updated": updated,
            "filled": filled,
            "failed": failed,
            "skipped": skipped,
            **backfill,
        }

    async def _sanitize_sync_bot_ids(self) -> None:
        await OrderRecord.filter(bot_id="broker_sync").update(bot_id=None)
        await TradeOutcomeRecord.filter(bot_id="broker_sync").update(bot_id=None)

    async def _repair_sync_trade_identity(self, *, limit: int) -> None:
        sync_orders = (
            await OrderRecord.filter(signal_source="alpaca_sync")
            .order_by("-created_at")
            .limit(limit)
        )
        for sync_order in sync_orders:
            canonical_order = await _find_canonical_order_for_sync_order(sync_order)
            if canonical_order is not None and canonical_order.bot_id and canonical_order.bot_id.strip():
                if sync_order.bot_id != canonical_order.bot_id:
                    sync_order.bot_id = canonical_order.bot_id
                    await sync_order.save(update_fields=["bot_id"])
                await TradeOutcomeRecord.filter(order=sync_order).update(bot_id=canonical_order.bot_id)
                continue

            inferred_bot_id = _infer_sync_bot_id_from_order(sync_order)
            if inferred_bot_id == "unknown":
                continue
            if sync_order.bot_id != inferred_bot_id:
                sync_order.bot_id = inferred_bot_id
                await sync_order.save(update_fields=["bot_id"])
            await TradeOutcomeRecord.filter(order=sync_order).update(bot_id=inferred_bot_id)

    async def _load_active_executions(self, *, limit: int) -> list[_ActiveExecution]:
        rows = (
            await ExecutionRecord.all()
            .select_related("order")
            .order_by("-created_at")
            .limit(max(limit * 5, limit))
        )
        latest_by_order: dict[int, _ActiveExecution] = {}
        for row in rows:
            order = row.order
            if order is None:
                continue
            order_id = int(order.id)
            if order_id in latest_by_order:
                continue
            if order.status not in {OrderStatus.REQUESTED.value, OrderStatus.SUBMITTED.value}:
                continue
            latest_by_order[order_id] = _ActiveExecution(
                order_id=order_id,
                broker_name=order.broker_name,
                broker_order_id=row.broker_order_id,
                current_status=row.status,
                current_filled_qty=float(row.filled_qty),
                current_avg_price=float(row.avg_price) if row.avg_price is not None else None,
            )
            if len(latest_by_order) >= limit:
                break
        return list(latest_by_order.values())

    async def _backfill_recent_orders(self, *, limit: int) -> dict[str, object]:
        stats: dict[str, int] = {
            "backfill_checked": 0,
            "backfill_imported": 0,
            "backfill_executions": 0,
            "backfill_filled": 0,
            "backfill_skipped": 0,
            "backfill_errors": 0,
        }
        safe_limit = max(1, min(limit, 300))
        for broker_name in self._broker_router.list_brokers():
            broker = self._broker_router.get(broker_name)
            if broker is None:
                continue
            list_orders = getattr(broker, "list_orders", None)
            if not callable(list_orders):
                continue
            try:
                raw_rows = await list_orders(status="all", limit=safe_limit)
            except TypeError:
                raw_rows = await list_orders(limit=safe_limit)
            except Exception as exc:
                stats["backfill_errors"] += 1
                pipeline_logger.log(
                    "WARNING",
                    "pipeline.{} {}",
                    "broker.backfill_failed",
                    format_log_fields(
                        {
                            "broker_name": broker_name,
                            "reason": str(exc)[:200],
                        }
                    ),
                )
                continue
            if not isinstance(raw_rows, list):
                continue

            async with UnitOfWork() as uow:
                signals_repo = SignalsRepository(connection=uow.connection)
                orders_repo = OrdersRepository(connection=uow.connection)
                executions_repo = ExecutionsRepository(connection=uow.connection)
                outcomes_repo = TradeOutcomesRepository(connection=uow.connection)

                for raw_row in raw_rows:
                    if not isinstance(raw_row, dict):
                        stats["backfill_skipped"] += 1
                        continue
                    normalized = _normalize_broker_order(raw_row, broker_name=broker_name)
                    if normalized is None:
                        stats["backfill_skipped"] += 1
                        continue
                    stats["backfill_checked"] += 1
                    try:
                        imported, saved_execution, opened_outcome = await self._upsert_broker_order(
                            order=normalized,
                            signals_repo=signals_repo,
                            orders_repo=orders_repo,
                            executions_repo=executions_repo,
                            outcomes_repo=outcomes_repo,
                        )
                    except Exception as exc:
                        stats["backfill_errors"] += 1
                        pipeline_logger.log(
                            "WARNING",
                            "pipeline.{} {}",
                            "broker.backfill_order_failed",
                            format_log_fields(
                                {
                                    "broker_name": broker_name,
                                    "broker_order_id": normalized.broker_order_id,
                                    "symbol": normalized.symbol,
                                    "reason": str(exc)[:200],
                                }
                            ),
                        )
                        continue
                    if imported:
                        stats["backfill_imported"] += 1
                    if saved_execution:
                        stats["backfill_executions"] += 1
                    if opened_outcome:
                        stats["backfill_filled"] += 1
        return stats

    async def _upsert_broker_order(
        self,
        *,
        order: _BrokerOrder,
        signals_repo: SignalsRepository,
        orders_repo: OrdersRepository,
        executions_repo: ExecutionsRepository,
        outcomes_repo: TradeOutcomesRepository,
    ) -> tuple[bool, bool, bool]:
        sync_key = f"broker_sync:{order.broker_name}:{order.broker_order_id}"
        canonical_order_id = await _find_canonical_order_id_by_broker_order_id(
            broker_order_id=order.broker_order_id,
        )
        existing_order = await OrderRecord.filter(idempotency_key=sync_key).first()
        imported = False
        ownership = build_signal_ownership(
            source=f"{order.broker_name}_sync",
            symbol=order.symbol,
            lane_hint="broker_sync",
            strategy_hint="broker_sync",
            metadata={"market": order.market},
        )
        if canonical_order_id is not None:
            order_id = canonical_order_id
        elif existing_order is None:
            signal = Signal(
                signal_id=sync_key,
                symbol=order.symbol,
                action=order.action,
                score=0.5,
                confidence=0.5,
                source=f"{order.broker_name}_sync",
                lane_hint="broker_sync",
                strategy_hint="broker_sync",
                status=_signal_status(order.status),
                metadata={
                    "broker_sync": True,
                    "broker_name": order.broker_name,
                    "broker_order_id": order.broker_order_id,
                    "broker_status": order.status,
                    "market": order.market,
                    **ownership,
                },
                created_at=order.timestamp,
            )
            await signals_repo.save_signal(signal)
            order_intent = OrderIntent(
                signal_id=sync_key,
                symbol=order.symbol,
                action=order.action,
                quantity=order.quantity,
                idempotency_key=sync_key,
                broker_name=order.broker_name,
                market=order.market,
                order_type=order.order_type,
                lane_hint="broker_sync",
                strategy_hint="broker_sync",
                metadata={
                    "signal_source": f"{order.broker_name}_sync",
                    "broker_sync": True,
                    "broker_order_id": order.broker_order_id,
                    "market": order.market,
                    **ownership,
                },
                created_at=order.timestamp,
            )
            order_id = await orders_repo.create_order_intent(order_intent)
            imported = True
        else:
            order_id = str(existing_order.id)

        status = _parse_execution_status(order.status)
        should_write_execution = await _execution_changed(
            order_id=order_id,
            status=status,
            broker_order_id=order.broker_order_id,
            filled_qty=order.filled_qty,
            avg_price=order.avg_price,
        )
        if not should_write_execution:
            return imported, False, False

        execution = ExecutionResult(
            order_id=order_id,
            status=status,
            broker_order_id=order.broker_order_id,
            filled_qty=order.filled_qty,
            avg_price=order.avg_price,
            created_at=order.timestamp,
        )
        await executions_repo.save_execution(execution)
        if status in {ExecutionStatus.FILLED, ExecutionStatus.EXECUTED}:
            await outcomes_repo.record_execution_entry(execution)
            return imported, True, True
        return imported, True, False


def _parse_execution_status(raw_status: object) -> ExecutionStatus:
    if not isinstance(raw_status, str):
        return ExecutionStatus.FAILED
    normalized = raw_status.strip().lower()
    status_map: dict[str, ExecutionStatus] = {
        "submitted": ExecutionStatus.SUBMITTED,
        "new": ExecutionStatus.SUBMITTED,
        "accepted": ExecutionStatus.SUBMITTED,
        "pending_new": ExecutionStatus.SUBMITTED,
        "accepted_for_bidding": ExecutionStatus.SUBMITTED,
        "partial": ExecutionStatus.PARTIAL,
        "partially_filled": ExecutionStatus.PARTIAL,
        "filled": ExecutionStatus.FILLED,
        "executed": ExecutionStatus.EXECUTED,
        "failed": ExecutionStatus.FAILED,
        "error": ExecutionStatus.FAILED,
        "not_found": ExecutionStatus.FAILED,
        "rejected": ExecutionStatus.REJECTED,
        "canceled": ExecutionStatus.CANCELED,
        "cancelled": ExecutionStatus.CANCELED,
        "expired": ExecutionStatus.EXPIRED,
    }
    return status_map.get(normalized, ExecutionStatus.FAILED)


def _pick_optional_float(payload: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            return float(cast(float | int | str, value))
        except (TypeError, ValueError):
            continue
    return None


def _pick_float(payload: dict[str, object], *keys: str) -> float:
    value = _pick_optional_float(payload, *keys)
    if value is None:
        return 0.0
    return value


def _normalize_broker_order(payload: dict[str, object], *, broker_name: str) -> _BrokerOrder | None:
    broker_order_id = _pick_text(payload, "id", "broker_order_id", "order_id")
    symbol = _pick_text(payload, "symbol")
    side = _pick_text(payload, "side", "action")
    status = _pick_text(payload, "status")
    if broker_order_id is None or symbol is None or side is None or status is None:
        return None
    action = OrderAction.BUY if side.strip().lower() == "buy" else OrderAction.SELL
    qty = _pick_float(payload, "qty", "quantity")
    filled_qty = _pick_float(payload, "filled_qty", "qty", "quantity")
    quantity = qty if qty > 0 else filled_qty
    if quantity <= 0:
        return None
    order_type = (_pick_text(payload, "type", "order_type") or "market").strip().lower()
    timestamp = (
        _parse_datetime(payload.get("updated_at"))
        or _parse_datetime(payload.get("filled_at"))
        or _parse_datetime(payload.get("submitted_at"))
        or _parse_datetime(payload.get("created_at"))
        or datetime.now(timezone.utc)
    )
    normalized_symbol = symbol.upper().strip()
    return _BrokerOrder(
        broker_name=broker_name.strip().lower(),
        broker_order_id=broker_order_id.strip(),
        symbol=normalized_symbol,
        action=action,
        quantity=quantity,
        filled_qty=filled_qty,
        avg_price=_pick_optional_float(payload, "filled_avg_price", "avg_price", "price"),
        status=status.strip().lower(),
        order_type=order_type,
        market=_infer_market(normalized_symbol),
        timestamp=timestamp,
    )


def _signal_status(raw_status: str) -> SignalStatus:
    status = _parse_execution_status(raw_status)
    if status in {ExecutionStatus.FILLED, ExecutionStatus.EXECUTED}:
        return SignalStatus.EXECUTED
    if status in {
        ExecutionStatus.FAILED,
        ExecutionStatus.REJECTED,
        ExecutionStatus.CANCELED,
        ExecutionStatus.EXPIRED,
    }:
        return SignalStatus.FAILED
    return SignalStatus.APPROVED


def _pick_text(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _infer_market(symbol: str) -> str:
    if symbol.upper().endswith(".AX"):
        return "asx_equities"
    return "us_equities"


async def _execution_changed(
    *,
    order_id: str,
    status: ExecutionStatus,
    broker_order_id: str | None,
    filled_qty: float,
    avg_price: float | None,
) -> bool:
    try:
        numeric_order_id = int(order_id)
    except ValueError:
        return True
    latest = await ExecutionRecord.filter(order_id=numeric_order_id).order_by("-created_at").first()
    if latest is None:
        return True
    if latest.status != status.value:
        return True
    if (latest.broker_order_id or None) != (broker_order_id or None):
        return True
    if abs(float(latest.filled_qty) - filled_qty) > 1e-9:
        return True
    latest_avg = float(latest.avg_price) if latest.avg_price is not None else None
    if latest_avg is None and avg_price is None:
        return False
    if latest_avg is None or avg_price is None:
        return True
    return abs(latest_avg - avg_price) > 1e-9


async def _find_canonical_order_id_by_broker_order_id(*, broker_order_id: str) -> str | None:
    executions = await ExecutionRecord.filter(broker_order_id=broker_order_id).select_related("order").all()
    canonical = _pick_canonical_order_from_executions(executions)
    if canonical is None:
        return None
    return str(canonical.id)


async def _find_canonical_order_for_sync_order(sync_order: OrderRecord) -> OrderRecord | None:
    execution = await ExecutionRecord.filter(order=sync_order).order_by("-created_at").first()
    if execution is None or not execution.broker_order_id:
        return None
    executions = await ExecutionRecord.filter(broker_order_id=execution.broker_order_id).select_related("order").all()
    canonical = _pick_canonical_order_from_executions(executions)
    if canonical is None or canonical.id == sync_order.id:
        return None
    return canonical


def _pick_canonical_order_from_executions(executions: list[ExecutionRecord]) -> OrderRecord | None:
    fallback: OrderRecord | None = None
    for execution in executions:
        order = execution.order
        if order is None:
            continue
        if fallback is None:
            fallback = order
        if not str(order.idempotency_key or "").startswith("broker_sync:"):
            return order
    return fallback


def _infer_sync_bot_id_from_order(order: OrderRecord) -> str:
    ownership = build_signal_ownership(
        source=str(order.signal_source or "unknown"),
        symbol=order.symbol,
        lane_hint="broker_sync",
        strategy_hint="broker_sync",
        metadata={"market": order.market or ""},
    )
    return str(ownership.get("bot_id", "unknown"))
