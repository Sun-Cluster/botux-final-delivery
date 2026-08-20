from __future__ import annotations

from datetime import datetime

from db.models import OutboxEvent
from db.repositories.base import RepositoryBase
from domain.enums import OutboxStatus


class OutboxRepository(RepositoryBase):
    async def list_recent(self, limit: int = 50) -> list[dict[str, object]]:
        rows = await self._query(OutboxEvent.all()).order_by("-created_at").limit(limit)
        result: list[dict[str, object]] = []
        for row in rows:
            created_at: datetime | None = row.created_at
            result.append(
                {
                    "id": int(row.id),
                    "event_key": row.event_key,
                    "event_type": row.event_type,
                    "payload": row.payload,
                    "status": row.status,
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        return result

    async def list_pending(self, limit: int = 100) -> list[OutboxEvent]:
        return await self._query(
            OutboxEvent.filter(status=OutboxStatus.PENDING.value)
        ).order_by("created_at").limit(limit)

    async def mark_processed(self, event_id: int) -> bool:
        row = await self._query(OutboxEvent.filter(id=event_id)).first()
        if row is None:
            return False
        row.status = OutboxStatus.PROCESSED.value
        await self._save(row)
        return True

    async def mark_failed(self, event_id: int, reason: str) -> bool:
        row = await self._query(OutboxEvent.filter(id=event_id)).first()
        if row is None:
            return False
        payload = dict(row.payload or {})
        failures = payload.get("dispatch_failures")
        if not isinstance(failures, list):
            failures = []
        failures.append({"reason": reason[:300]})
        payload["dispatch_failures"] = failures
        row.payload = payload
        row.status = OutboxStatus.FAILED.value
        await self._save(row)
        return True
