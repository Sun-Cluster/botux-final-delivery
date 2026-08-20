from __future__ import annotations

from loguru import logger
from datetime import datetime
from decimal import Decimal

from runtime.logging import format_log_fields
from db.models import ExecutionRecord, OrderRecord, SignalRecord
from db.repositories._common import append_outbox_event
from db.repositories.base import RepositoryBase
from domain.enums import ExecutionStatus, OrderStatus, SignalStatus
from domain.models.execution_result import ExecutionResult


pipeline_logger = logger.bind(pipeline_module=__name__)

class ExecutionsRepository(RepositoryBase):
    async def save_execution(self, execution: ExecutionResult) -> None:
        try:
            order_pk = int(execution.order_id)
        except ValueError as exc:
            raise ValueError(f"invalid order_id: {execution.order_id}") from exc

        order = await self._query(OrderRecord.filter(id=order_pk).select_related("signal")).first()
        if order is None:
            raise ValueError(f"order not found: {execution.order_id}")

        record = ExecutionRecord(
            order=order,
            broker_order_id=execution.broker_order_id,
            status=execution.status.value,
            filled_qty=Decimal(str(execution.filled_qty)),
            avg_price=Decimal(str(execution.avg_price)) if execution.avg_price is not None else None,
            created_at=execution.created_at,
        )
        await self._save(record)

        if execution.status in {ExecutionStatus.FILLED, ExecutionStatus.EXECUTED}:
            order.status = OrderStatus.EXECUTED.value
            signal_status: SignalStatus | None = SignalStatus.EXECUTED
            event_type = "OrderExecuted"
        elif execution.status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.REJECTED,
            ExecutionStatus.CANCELED,
            ExecutionStatus.EXPIRED,
        }:
            order.status = OrderStatus.FAILED.value
            signal_status = SignalStatus.FAILED
            event_type = "OrderFailed"
        else:
            mapped_status = (
                OrderStatus.SUBMITTED if execution.status == ExecutionStatus.SUBMITTED else OrderStatus.REQUESTED
            )
            order.status = mapped_status.value
            signal_status = None
            event_type = "OrderExecutionUpdated"
        await self._save(order)

        signal_record = order.signal
        if signal_status is not None:
            if signal_record is not None:
                signal_record.status = signal_status.value
                signal_metadata = signal_record.metadata if isinstance(signal_record.metadata, dict) else {}
                if execution.status in {
                    ExecutionStatus.FAILED,
                    ExecutionStatus.REJECTED,
                    ExecutionStatus.CANCELED,
                    ExecutionStatus.EXPIRED,
                }:
                    failure_reason = execution.error_reason or execution.status.value
                    signal_record.blocked_reason = failure_reason
                    signal_metadata["failure_reason"] = failure_reason
                    signal_metadata["last_status_reason"] = failure_reason
                    signal_metadata["last_status_reason_status"] = signal_status.value
                else:
                    signal_record.blocked_reason = None
                    signal_metadata.pop("failure_reason", None)
                signal_record.metadata = signal_metadata
                await self._save(signal_record)

        await append_outbox_event(
            event_type=event_type,
            entity_key=str(order.id),
            payload={
                "order_id": order.id,
                "signal_id": signal_record.signal_id if signal_record is not None else None,
                "execution_id": record.id,
                "status": execution.status.value,
                "broker_order_id": execution.broker_order_id,
                "filled_qty": execution.filled_qty,
                "avg_price": execution.avg_price,
                "error_reason": execution.error_reason,
                "broker_name": order.broker_name,
                "market": order.market,
                "order_type": order.order_type,
                "schema_version": execution.schema_version,
            },
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "execution.stored", format_log_fields({"order_id": order.id, "signal_id": signal_record.signal_id if signal_record is not None else None, "execution_id": record.id, "status": execution.status.value, "error_reason": execution.error_reason, "broker_order_id": execution.broker_order_id, "filled_qty": execution.filled_qty, "avg_price": execution.avg_price, "broker_name": order.broker_name, "market": order.market, "order_type": order.order_type, "signal_status": signal_status.value if signal_status is not None else None}))

    async def count_since(
        self,
        since: datetime,
        *,
        statuses: set[ExecutionStatus] | None = None,
    ) -> int:
        query = self._query(ExecutionRecord.filter(created_at__gte=since))
        if statuses:
            query = query.filter(status__in=[status.value for status in statuses])
        return int(await query.count())

    async def list_since(self, since: datetime, *, limit: int = 200) -> list[ExecutionRecord]:
        rows = (
            await self._query(ExecutionRecord.filter(created_at__gte=since))
            .select_related("order__signal")
            .order_by("-created_at")
            .limit(limit)
        )
        return list(rows)
