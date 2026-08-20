from __future__ import annotations

from db.models import AuditLog
from db.repositories._common import JSONValue
from db.repositories.base import RepositoryBase


class AuditLogsRepository(RepositoryBase):
    async def append(
        self,
        *,
        event_type: str,
        payload: dict[str, JSONValue],
        actor: str | None = None,
        trace_id: str | None = None,
    ) -> int:
        record = AuditLog(
            trace_id=trace_id,
            actor=actor,
            event_type=event_type,
            payload=payload,
        )
        await self._save(record)
        return int(record.id)

    async def list_recent(self, *, limit: int = 100) -> list[AuditLog]:
        rows = await self._query(AuditLog.all()).order_by("-created_at").limit(limit)
        return list(rows)

    async def list_recent_by_prefix(self, *, prefix: str, limit: int = 100) -> list[AuditLog]:
        rows = await self._query(AuditLog.filter(event_type__startswith=prefix)).order_by("-created_at").limit(limit)
        return list(rows)

    async def latest_by_type(self, *, event_type: str) -> AuditLog | None:
        return await self._query(AuditLog.filter(event_type=event_type)).order_by("-created_at").first()
