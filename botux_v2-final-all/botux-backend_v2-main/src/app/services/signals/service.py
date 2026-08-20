from __future__ import annotations

from loguru import logger
from runtime.logging import format_log_fields
from db.repositories.signals_repo import SignalsRepository
from db.uow import UnitOfWork
from domain.models.signal import Signal


pipeline_logger = logger.bind(pipeline_module=__name__)

class SignalService:
    async def ingest_signal(self, signal: Signal) -> Signal:
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.ingest", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint, "status": signal.status.value}))
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            await repo.save_signal(signal)
        return signal
