from __future__ import annotations

from db.repositories.signals_repo import SignalsRepository
from db.uow import UnitOfWork
from domain.enums import SignalStatus


async def requeue_signal(signal_id: str, *, reason: str = "manual_requeue") -> bool:
    async with UnitOfWork() as uow:
        repo = SignalsRepository(connection=uow.connection)
        return await repo.set_status(signal_id, SignalStatus.PENDING, reason=reason)
