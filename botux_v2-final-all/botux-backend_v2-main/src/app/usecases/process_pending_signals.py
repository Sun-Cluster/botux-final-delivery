from __future__ import annotations

from loguru import logger
from typing import TYPE_CHECKING

from app.services.execution.service import ExecutionService
from app.services.runtime_config.service import RuntimeConfigService
from runtime.logging import format_log_fields
from app.usecases.submit_order import submit_order
from db.repositories.signals_repo import SignalsRepository
from db.uow import UnitOfWork

pipeline_logger = logger.bind(pipeline_module=__name__)

if TYPE_CHECKING:
    from runtime.process_manager import ProcessManager


async def process_pending_signals(
    *,
    limit: int = 100,
    quantity: float = 1.0,
    execution_service: ExecutionService | None = None,
    process_manager: ProcessManager | None = None,
) -> dict[str, int]:
    async with UnitOfWork() as uow:
        signals_repo = SignalsRepository(connection=uow.connection)
        runtime_configs = RuntimeConfigService(connection=uow.connection)
        max_attempts_config = await runtime_configs.resolve("signal.max_retries")
        try:
            max_attempts = (
                int(max_attempts_config.value) if max_attempts_config.value is not None else 3
            )
        except (TypeError, ValueError):
            max_attempts = 3

        await signals_repo.auto_retry_failed_signals(max_attempts=max_attempts, limit=limit)
        pending_signals = await signals_repo.list_pending(limit=limit)

    stats = {"processed": 0, "executed": 0, "rejected": 0, "errors": 0, "enqueued": 0}
    if process_manager is not None:
        for signal in pending_signals:
            pipeline_logger.log("INFO", "pipeline.{} {}", "signal.processing_queued", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "runner": "queue"}))
            await process_manager.publish_signal(signal)
            stats["processed"] += 1
            stats["enqueued"] += 1
        return stats

    for signal in pending_signals:
        try:
            stats["processed"] += 1
            pipeline_logger.log("INFO", "pipeline.{} {}", "signal.processing_started", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "runner": "inline"}))
            result = await submit_order(
                signal,
                quantity=quantity,
                execution_service=execution_service,
            )
            if result is None:
                stats["rejected"] += 1
                pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.processing_finished", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "result": "rejected", "runner": "inline"}))
            else:
                stats["executed"] += 1
                pipeline_logger.log("INFO", "pipeline.{} {}", "signal.processing_finished", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "result": result.status.value, "runner": "inline", "order_id": result.order_id}))
        except Exception:
            stats["errors"] += 1
            pipeline_logger.log("ERROR", "pipeline.{} {}", "signal.processing_failed", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "runner": "inline"}))
    return stats
