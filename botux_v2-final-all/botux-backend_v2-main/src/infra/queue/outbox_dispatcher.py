from __future__ import annotations

from db.repositories.outbox_repo import OutboxRepository
from db.uow import UnitOfWork
from infra.queue.bus import InProcessQueueBus
from infra.queue.envelope import QueueEnvelope

DISPATCHABLE_EVENTS: dict[str, str] = {
    "SignalCreated": "signal.process",
}


class OutboxDispatcher:
    def __init__(self, bus: InProcessQueueBus) -> None:
        self._bus = bus

    async def dispatch_pending(self, *, limit: int = 100) -> dict[str, int]:
        async with UnitOfWork() as uow:
            repo = OutboxRepository(connection=uow.connection)
            events = await repo.list_pending(limit=limit)

        stats = {"checked": len(events), "dispatched": 0, "processed": 0, "failed": 0, "skipped": 0}
        for event in events:
            msg_type = DISPATCHABLE_EVENTS.get(event.event_type)
            if msg_type is None:
                async with UnitOfWork() as uow:
                    repo = OutboxRepository(connection=uow.connection)
                    await repo.mark_processed(int(event.id))
                stats["processed"] += 1
                stats["skipped"] += 1
                continue
            try:
                await self._bus.publish(
                    QueueEnvelope(
                        msg_id=f"outbox:{event.id}:{event.event_key}",
                        msg_type=msg_type,
                        payload=event.payload,
                        trace_id=str(event.payload.get("signal_id") or event.event_key),
                    )
                )
            except Exception as exc:
                async with UnitOfWork() as uow:
                    repo = OutboxRepository(connection=uow.connection)
                    await repo.mark_failed(int(event.id), str(exc))
                stats["failed"] += 1
                continue

            async with UnitOfWork() as uow:
                repo = OutboxRepository(connection=uow.connection)
                await repo.mark_processed(int(event.id))
            stats["dispatched"] += 1
            stats["processed"] += 1
        return stats
