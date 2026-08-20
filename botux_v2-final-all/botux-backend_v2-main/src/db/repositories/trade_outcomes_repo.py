from __future__ import annotations

from loguru import logger
from datetime import datetime
from typing import Any, cast

from runtime.logging import format_log_fields
from db.models import OrderRecord, SignalRecord, TradeOutcomeRecord
from db.repositories._common import JSONValue, append_outbox_event, utc_now
from db.repositories.base import RepositoryBase
from domain.enums import OrderAction, TradeOutcomeStatus
from domain.exit_reasons import normalize_exit_reason
from domain.models.execution_result import ExecutionResult
from domain.models.trade_outcome import TradeOutcome


pipeline_logger = logger.bind(pipeline_module=__name__)

class TradeOutcomesRepository(RepositoryBase):
    async def save_outcome(self, outcome: TradeOutcome) -> None:
        signal = await self._query(SignalRecord.filter(signal_id=outcome.signal_id)).first()
        if signal is None:
            raise ValueError(f"signal not found: {outcome.signal_id}")

        order = await self._resolve_order(outcome.trade_id)
        features = self._build_features(outcome)
        record = await self._find_by_trade_id(outcome.trade_id)
        close_reason = normalize_exit_reason(outcome.close_reason) if outcome.close_reason is not None else None
        if record is None:
            record = TradeOutcomeRecord(
                signal=signal,
                order=order,
                symbol=outcome.symbol,
                outcome=outcome.outcome.value,
                pnl_pct=outcome.pnl_pct,
                trade_id=outcome.trade_id,
                action=_optional_text(outcome.action),
                quantity=outcome.quantity,
                entry_price=outcome.entry_price,
                exit_price=outcome.exit_price,
                close_reason=close_reason,
                bot_id=outcome.bot_id,
                source=outcome.source,
                broker_order_id=outcome.broker_order_id,
                broker_name=outcome.broker_name,
                market=outcome.market,
                order_type=outcome.order_type,
                features=_residual_trade_features(features),
                created_at=outcome.opened_at,
                closed_at=outcome.closed_at,
            )
        else:
            record.signal = signal
            record.order = cast(Any, order)
            record.symbol = outcome.symbol
            record.outcome = outcome.outcome.value
            record.pnl_pct = outcome.pnl_pct
            record.trade_id = outcome.trade_id
            record.action = _optional_text(outcome.action)
            record.quantity = outcome.quantity
            record.entry_price = outcome.entry_price
            record.exit_price = outcome.exit_price
            record.close_reason = cast(Any, close_reason)
            record.bot_id = outcome.bot_id
            record.source = outcome.source
            record.broker_order_id = outcome.broker_order_id
            record.broker_name = outcome.broker_name
            record.market = outcome.market
            record.order_type = outcome.order_type
            record.features = _residual_trade_features({**_feature_dict(record.features), **features})
            record.closed_at = outcome.closed_at
        await self._save(record)
        await append_outbox_event(
            event_type="TradeOutcomeRecorded",
            entity_key=outcome.trade_id,
            payload=_json_payload(
                {
                "trade_outcome_id": record.id,
                "trade_id": outcome.trade_id,
                "signal_id": outcome.signal_id,
                "symbol": outcome.symbol,
                "outcome": outcome.outcome.value,
                "pnl_pct": outcome.pnl_pct,
                "closed_at": outcome.closed_at.isoformat() if outcome.closed_at else None,
                "action": record.action,
                "quantity": record.quantity,
                "entry_price": record.entry_price,
                "exit_price": record.exit_price,
                "close_reason": record.close_reason,
                "bot_id": record.bot_id,
                "source": record.source,
                "broker_order_id": record.broker_order_id,
                "broker_name": record.broker_name,
                "market": record.market,
                "order_type": record.order_type,
                "features": _residual_trade_features(features),
                }
            ),
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "trade_outcome.recorded", format_log_fields({"trade_id": outcome.trade_id, "signal_id": outcome.signal_id, "symbol": outcome.symbol, "outcome": outcome.outcome.value, "pnl_pct": outcome.pnl_pct}))

    async def record_execution_entry(self, execution: ExecutionResult) -> TradeOutcomeRecord | None:
        if execution.avg_price is None or execution.filled_qty <= 0:
            return None
        try:
            order_pk = int(execution.order_id)
        except ValueError as exc:
            raise ValueError(f"invalid order_id: {execution.order_id}") from exc

        order = await self._query(OrderRecord.filter(id=order_pk).select_related("signal")).first()
        if order is None or order.signal is None:
            raise ValueError(f"order not found: {execution.order_id}")

        if str(order.action or "").lower() == OrderAction.SELL.value:
            close_reason = _resolve_execution_close_reason(order)
            closed = await self.close_open_outcome(
                symbol=order.symbol,
                exit_price=float(execution.avg_price),
                reason=close_reason,
                closed_at=execution.created_at,
            )
            if closed is not None:
                return closed

        trade_id = str(order.id)
        existing = await self._find_open_or_trade_id(trade_id=trade_id, symbol=order.symbol)
        signal_features = _feature_dict(order.signal.metadata)
        option_position = _feature_dict(signal_features.get("option_position"))
        action = order.action
        quantity = float(execution.filled_qty)
        entry_price = float(execution.avg_price)
        features = {
            "schema_version": "v1",
            "signal_id": order.signal.signal_id,
            "entry_reason": order.signal.signal_id,
            "status_transitions": [
                {
                    "status": TradeOutcomeStatus.OPEN.value,
                    "at": execution.created_at.isoformat(),
                    "reason": "execution_filled",
                }
            ],
            "candidate": _feature_dict(signal_features.get("candidate")),
            "option_position": option_position,
            "underlying": _optional_text(signal_features.get("underlying_symbol"))
            or _optional_text(option_position.get("underlying"))
            or order.symbol,
            "contract": _optional_text(signal_features.get("option_contract"))
            or _optional_text(option_position.get("contract"))
            or order.symbol,
            "type": _optional_text(option_position.get("type")),
            "strike": _optional_float(option_position.get("strike")),
            "expiration": _optional_text(option_position.get("expiration")),
            "premium_paid": (
                float(execution.avg_price) * quantity * 100.0
                if order.market == "options_us"
                else float(execution.avg_price) * quantity
            ),
        }
        bot_id = _resolve_bot_id(order)
        source = order.signal.source or "unknown"
        if existing is None:
            record = TradeOutcomeRecord(
                signal=order.signal,
                order=order,
                symbol=order.symbol,
                outcome=TradeOutcomeStatus.OPEN.value,
                pnl_pct=0.0,
                trade_id=trade_id,
                action=action,
                quantity=quantity,
                entry_price=entry_price,
                exit_price=None,
                close_reason=None,
                bot_id=bot_id,
                source=source,
                broker_order_id=execution.broker_order_id,
                broker_name=order.broker_name,
                market=order.market,
                order_type=order.order_type,
                features=_residual_trade_features(features),
                created_at=execution.created_at,
                closed_at=None,
            )
        else:
            record = existing
            record.signal = order.signal
            record.order = cast(Any, order)
            record.outcome = TradeOutcomeStatus.OPEN.value
            record.pnl_pct = 0.0
            record.closed_at = None
            record.trade_id = trade_id
            record.action = action
            record.quantity = quantity
            record.entry_price = entry_price
            record.exit_price = None
            record.close_reason = cast(Any, None)
            record.bot_id = bot_id
            record.source = source
            record.broker_order_id = execution.broker_order_id
            record.broker_name = order.broker_name
            record.market = order.market
            record.order_type = order.order_type
            record.features = _residual_trade_features({**_feature_dict(record.features), **features})
        await self._save(record)
        await append_outbox_event(
            event_type="TradeOutcomeOpened",
            entity_key=trade_id,
            payload=_json_payload(
                {
                "trade_outcome_id": record.id,
                "trade_id": trade_id,
                "signal_id": order.signal.signal_id,
                "symbol": order.symbol,
                "entry_price": entry_price,
                "quantity": quantity,
                "source": source,
                "bot_id": bot_id,
                "broker_name": order.broker_name,
                "market": order.market,
                "order_type": order.order_type,
                "action": action,
                "broker_order_id": execution.broker_order_id,
                }
            ),
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "trade_outcome.opened", format_log_fields({"trade_id": trade_id, "signal_id": order.signal.signal_id, "symbol": order.symbol, "entry_price": entry_price, "quantity": quantity, "source": source, "bot_id": bot_id, "broker_name": order.broker_name, "market": order.market, "order_type": order.order_type}))
        return record

    async def close_open_outcome(
        self,
        *,
        symbol: str,
        exit_price: float,
        reason: str,
        closed_at: datetime | None = None,
    ) -> TradeOutcomeRecord | None:
        normalized_reason = normalize_exit_reason(reason)
        record = (
            await self._query(TradeOutcomeRecord.filter(symbol=symbol.upper(), outcome="open"))
            .select_related("signal", "order")
            .order_by("-created_at")
            .first()
        )
        if record is None and symbol.upper().endswith(".AX"):
            record = (
                await self._query(TradeOutcomeRecord.filter(symbol=symbol.upper()[:-3], outcome="open"))
                .select_related("signal", "order")
                .order_by("-created_at")
                .first()
            )
        if record is None:
            return None

        features = dict(record.features or {})
        entry_price = record.entry_price if record.entry_price is not None else 0.0
        if entry_price <= 0 and record.order is not None:
            execution = (
                await self._query(cast(Any, record.order).executions.all())
                .filter(avg_price__not_isnull=True)
                .order_by("created_at")
                .first()
            )
            if execution is not None and execution.avg_price is not None:
                entry_price = float(execution.avg_price)
        action = str(record.action or (cast(Any, record.order).action if record.order is not None else "buy")).lower()
        pnl_pct = _calculate_pnl_pct(entry_price=entry_price, exit_price=exit_price, action=action)
        status = TradeOutcomeStatus.WIN if pnl_pct > 0 else TradeOutcomeStatus.LOSS
        now = closed_at or utc_now()
        opened_at = record.created_at
        hold_hours = None
        if isinstance(opened_at, datetime):
            hold_hours = round((now - opened_at).total_seconds() / 3600.0, 3)
        transition = {"status": status.value, "at": now.isoformat(), "reason": normalized_reason}
        transitions = features.get("status_transitions")
        if not isinstance(transitions, list):
            transitions = []
        transitions.append(transition)
        features.update(
            {
                "hold_hours": hold_hours,
                "status_transitions": transitions,
            }
        )
        if normalized_reason != reason:
            features["close_reason_raw"] = reason
        record.outcome = status.value
        record.pnl_pct = round(pnl_pct, 6)
        record.entry_price = entry_price if entry_price > 0 else record.entry_price
        record.exit_price = float(exit_price)
        record.close_reason = normalized_reason
        record.closed_at = now
        record.features = _residual_trade_features(features)
        await self._save(record)
        trade_id = str(record.trade_id or getattr(record, "order_id", None) or record.id)
        await append_outbox_event(
            event_type="TradeOutcomeClosed",
            entity_key=trade_id,
            payload=_json_payload(
                {
                "trade_outcome_id": record.id,
                "trade_id": trade_id,
                "signal_id": record.signal.signal_id if record.signal is not None else None,
                "symbol": record.symbol,
                "outcome": status.value,
                "pnl_pct": record.pnl_pct,
                "exit_price": float(exit_price),
                "close_reason": normalized_reason,
                "closed_at": now.isoformat(),
                }
            ),
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "trade_outcome.closed", format_log_fields({"trade_id": trade_id, "signal_id": record.signal.signal_id if record.signal is not None else None, "symbol": record.symbol, "outcome": status.value, "pnl_pct": record.pnl_pct, "exit_price": float(exit_price), "close_reason": normalized_reason, "closed_at": now}))
        return record

    async def list_closed_rows(self, limit: int = 1000) -> list[TradeOutcomeRecord]:
        return (
            await self._query(TradeOutcomeRecord.filter(outcome__in=["win", "loss", "breakeven"]))
            .select_related("signal", "order")
            .order_by("-closed_at", "-created_at")
            .limit(limit)
        )

    async def list_closed_rows_for_bot(
        self,
        bot_id: str,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[TradeOutcomeRecord]:
        query = self._query(
            TradeOutcomeRecord.filter(
                bot_id=bot_id.strip().lower(),
                outcome__in=["win", "loss", "breakeven"],
            )
        )
        if since is not None:
            query = query.filter(closed_at__gte=since)
        return (
            await query
            .select_related("signal", "order")
            .order_by("-closed_at", "-created_at")
            .limit(limit)
        )

    async def list_open_rows(self, limit: int = 1000) -> list[TradeOutcomeRecord]:
        return (
            await self._query(TradeOutcomeRecord.filter(outcome="open"))
            .select_related("signal", "order")
            .order_by("-created_at")
            .limit(limit)
        )

    async def count_open(self) -> int:
        return int(await self._query(TradeOutcomeRecord.filter(outcome="open")).count())

    async def count_closed_since(self, since: datetime) -> int:
        return int(
            await self._query(
                TradeOutcomeRecord.filter(
                    outcome__in=["win", "loss", "breakeven"],
                    closed_at__gte=since,
                )
            ).count()
        )

    async def _resolve_order(self, trade_id: str) -> OrderRecord | None:
        try:
            numeric = int(trade_id)
        except ValueError:
            numeric = None

        if numeric is not None:
            order = await self._query(OrderRecord.filter(id=numeric)).first()
            if order is not None:
                return order

        return await self._query(OrderRecord.filter(idempotency_key=trade_id)).first()

    async def _find_open_or_trade_id(self, *, trade_id: str, symbol: str) -> TradeOutcomeRecord | None:
        existing = await self._find_by_trade_id(trade_id)
        if existing is not None:
            return existing
        return await self._query(
            TradeOutcomeRecord.filter(symbol=symbol.upper(), outcome=TradeOutcomeStatus.OPEN.value)
        ).first()

    async def _find_by_trade_id(self, trade_id: str) -> TradeOutcomeRecord | None:
        order = await self._resolve_order(trade_id)
        if order is not None:
            row = await self._query(TradeOutcomeRecord.filter(order=order)).first()
            if row is not None:
                return row
        row = await self._query(TradeOutcomeRecord.filter(trade_id=trade_id)).first()
        return row

    def _build_features(self, outcome: TradeOutcome) -> dict[str, object]:
        features: dict[str, object] = {
            **outcome.features,
            "schema_version": outcome.schema_version,
        }
        return _residual_trade_features(features)

    async def list_recent(self, limit: int = 100) -> list[TradeOutcome]:
        rows = (
            await self._query(TradeOutcomeRecord.all())
            .prefetch_related("signal")
            .order_by("-created_at")
            .limit(limit)
        )
        results: list[TradeOutcome] = []
        for row in rows:
            closed_at = row.closed_at
            if closed_at is None:
                closed_at = row.created_at or utc_now()
            if isinstance(closed_at, datetime):
                normalized_closed_at = closed_at
            else:
                normalized_closed_at = utc_now()
            trade_id = _resolve_trade_id(row)
            features = dict(row.features) if isinstance(row.features, dict) else {}
            signal_id = row.signal.signal_id if row.signal else ""
            results.append(
                TradeOutcome(
                    trade_id=trade_id,
                    signal_id=signal_id,
                    symbol=row.symbol,
                    outcome=row.outcome,
                    pnl_pct=row.pnl_pct,
                    opened_at=row.created_at or utc_now(),
                    closed_at=normalized_closed_at if row.outcome != "open" else None,
                    entry_price=row.entry_price,
                    exit_price=row.exit_price,
                    quantity=row.quantity,
                    close_reason=normalize_exit_reason(row.close_reason) if row.close_reason is not None else None,
                    bot_id=row.bot_id,
                    source=row.source,
                    action=row.action,
                    broker_order_id=row.broker_order_id,
                    broker_name=row.broker_name,
                    market=row.market,
                    order_type=row.order_type,
                    features=features,
                )
            )
        return results

    async def list_recent_db_truth(self, limit: int = 100) -> list[TradeOutcome]:
        return await self.list_recent(limit=limit)

    async def list_open_symbols(self) -> set[str]:
        query = self._query(TradeOutcomeRecord.filter(outcome="open"))
        rows = await query.values_list("symbol", flat=True)
        return {str(symbol).upper() for symbol in rows}

    async def has_open_symbol(self, symbol: str) -> bool:
        normalized = symbol.upper().strip()
        row = await self._query(
            TradeOutcomeRecord.filter(symbol=normalized, outcome=TradeOutcomeStatus.OPEN.value)
        ).first()
        return row is not None


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _calculate_pnl_pct(*, entry_price: float, exit_price: float, action: str) -> float:
    if entry_price <= 0:
        return 0.0
    if action == OrderAction.SELL.value:
        return ((entry_price - exit_price) / entry_price) * 100.0
    return ((exit_price - entry_price) / entry_price) * 100.0


def _resolve_bot_id(order: OrderRecord) -> str:
    if order.bot_id is not None and order.bot_id.strip():
        return order.bot_id.strip()
    return "unknown"


def _resolve_execution_close_reason(order: OrderRecord) -> str:
    signal_meta = _feature_dict(order.signal.metadata if order.signal is not None else {})
    for key in ("exit_reason", "close_reason", "reason"):
        value = signal_meta.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "manual_exit"


TRADE_FEATURES_FLATTENED_KEYS = {
    "trade_id",
    "action",
    "entry_price",
    "exit_price",
    "quantity",
    "close_reason",
    "bot_id",
    "source",
    "broker_order_id",
    "broker_name",
    "market",
    "order_type",
}


def _resolve_trade_id(row: TradeOutcomeRecord) -> str:
    if row.trade_id is not None and row.trade_id.strip():
        return row.trade_id
    return str(getattr(row, "order_id", None) or row.id)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _feature_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _residual_trade_features(features: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in features.items() if key not in TRADE_FEATURES_FLATTENED_KEYS}


def _json_payload(value: dict[str, object]) -> dict[str, JSONValue]:
    return cast(dict[str, JSONValue], value)
