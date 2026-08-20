from __future__ import annotations

from loguru import logger
from datetime import datetime
from decimal import Decimal
from typing import cast

from runtime.logging import format_log_fields
from db.models import OrderRecord, SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase
from domain.enums import OrderStatus
from domain.models.order_intent import OrderIntent


pipeline_logger = logger.bind(pipeline_module=__name__)

class OrdersRepository(RepositoryBase):
    async def create_order_intent(self, order: OrderIntent) -> str:
        existing = await self._query(OrderRecord.filter(idempotency_key=order.idempotency_key)).first()
        if existing is not None:
            return str(existing.id)

        signal = await self._query(SignalRecord.filter(signal_id=order.signal_id)).first()
        if signal is None:
            raise ValueError(f"signal not found: {order.signal_id}")

        record = OrderRecord(
            signal=signal,
            idempotency_key=order.idempotency_key,
            symbol=order.symbol,
            action=order.action.value,
            quantity=Decimal(str(order.quantity)),
            broker_name=order.broker_name,
            market=order.market,
            order_type=order.order_type,
            bot_id=_optional_text(order.metadata.get("bot_id")),
            signal_source=_optional_text(order.metadata.get("signal_source")),
            signal_score=_optional_float(order.metadata.get("signal_score")),
            route_reason=_optional_text(order.metadata.get("route_reason")),
            position_size_pct=_optional_float(order.metadata.get("position_size_pct")),
            stop_loss_pct=_optional_float(order.metadata.get("stop_loss_pct")),
            take_profit_pct=_optional_float(order.metadata.get("take_profit_pct")),
            limit_price=_optional_float(order.metadata.get("limit_price")),
            reference_price=_optional_float(order.metadata.get("reference_price")),
            entry_price=_optional_float(order.metadata.get("entry_price")),
            last_price=_optional_float(order.metadata.get("last_price")),
            take_profit_price=_optional_float(order.metadata.get("take_profit_price")),
            stop_loss_price=_optional_float(order.metadata.get("stop_loss_price")),
            status=OrderStatus.REQUESTED.value,
            created_at=order.created_at,
        )
        await self._save(record)
        await append_outbox_event(
            event_type="OrderRequested",
            entity_key=order.idempotency_key,
            payload=_json_payload(
                {
                "order_id": record.id,
                "signal_id": order.signal_id,
                "symbol": order.symbol,
                "action": order.action.value,
                "quantity": order.quantity,
                "idempotency_key": order.idempotency_key,
                "broker_name": order.broker_name,
                "market": order.market,
                "order_type": order.order_type,
                "bot_id": record.bot_id,
                "signal_source": record.signal_source,
                "signal_score": record.signal_score,
                "route_reason": record.route_reason,
                "position_size_pct": record.position_size_pct,
                "stop_loss_pct": record.stop_loss_pct,
                "take_profit_pct": record.take_profit_pct,
                "limit_price": record.limit_price,
                "reference_price": record.reference_price,
                "entry_price": record.entry_price,
                "last_price": record.last_price,
                "take_profit_price": record.take_profit_price,
                "stop_loss_price": record.stop_loss_price,
                "schema_version": order.schema_version,
                }
            ),
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "order.stored", format_log_fields({"order_id": record.id, "signal_id": order.signal_id, "symbol": order.symbol, "action": order.action.value, "quantity": order.quantity, "broker_name": order.broker_name, "market": order.market, "order_type": order.order_type, "status": OrderStatus.REQUESTED.value}))
        return str(record.id)

    async def set_status(self, order_id: str, status: OrderStatus) -> bool:
        try:
            numeric_id = int(order_id)
        except ValueError:
            return False

        record = await self._query(OrderRecord.filter(id=numeric_id)).first()
        if record is None:
            return False

        record.status = status.value
        await self._save(record)
        await append_outbox_event(
            event_type="OrderStatusUpdated",
            entity_key=order_id,
            payload={"order_id": order_id, "status": status.value},
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "order.status_updated", format_log_fields({"order_id": order_id, "status": status.value}))
        return True

    async def count_since(self, since: datetime, *, statuses: set[OrderStatus] | None = None) -> int:
        query = self._query(OrderRecord.filter(created_at__gte=since))
        if statuses:
            query = query.filter(status__in=[status.value for status in statuses])
        return int(await query.count())

    async def count_symbol_since(
        self,
        symbol: str,
        since: datetime,
        *,
        statuses: set[OrderStatus] | None = None,
        action: str | None = None,
    ) -> int:
        query = self._query(OrderRecord.filter(symbol=symbol.upper().strip(), created_at__gte=since))
        if statuses:
            query = query.filter(status__in=[status.value for status in statuses])
        if action is not None:
            query = query.filter(action=action.strip().lower())
        return int(await query.count())

    async def count_entry_orders_since(
        self,
        since: datetime,
        *,
        statuses: set[OrderStatus] | None = None,
    ) -> int:
        query = self._query(OrderRecord.filter(created_at__gte=since, action="buy"))
        if statuses:
            query = query.filter(status__in=[status.value for status in statuses])
        return int(await query.count())

    async def has_active_entry_for_symbol(self, symbol: str) -> bool:
        row = await self._query(
            OrderRecord.filter(
                symbol=symbol.upper().strip(),
                action="buy",
                status__in=[OrderStatus.SUBMITTED.value, OrderStatus.EXECUTED.value],
            )
        ).first()
        return row is not None

    async def has_active_exit_for_symbol(self, symbol: str, *, lane: str | None = None) -> bool:
        query = self._query(
            OrderRecord.filter(
                symbol=symbol.upper().strip(),
                action="sell",
                status__in=[OrderStatus.REQUESTED.value, OrderStatus.SUBMITTED.value],
            )
        )
        if lane is not None:
            lane_hint = lane.strip().lower()
            if lane_hint:
                query = query.filter(signal_source=lane_hint)
        row = await query.first()
        return row is not None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _json_payload(value: dict[str, object]) -> dict[str, JSONValue]:
    return cast(dict[str, JSONValue], value)
