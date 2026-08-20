from __future__ import annotations

from datetime import datetime
from typing import cast

from db.models import PositionSnapshot
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase


class PositionSnapshotsRepository(RepositoryBase):
    async def save_snapshot(self, snapshot_key: str, payload: dict[str, object]) -> int:
        row = await self._query(PositionSnapshot.filter(snapshot_key=snapshot_key)).first()
        if row is None:
            row = PositionSnapshot(snapshot_key=snapshot_key, payload=payload)
        else:
            row.payload = payload
        await self._save(row)
        await append_outbox_event(
            event_type="PositionSnapshotCaptured",
            entity_key=snapshot_key,
            payload={"snapshot_key": snapshot_key, "payload": cast(dict[str, JSONValue], payload)},
            connection=self._connection,
        )
        return int(row.id)

    async def list_recent(self, limit: int = 20) -> list[dict[str, object]]:
        rows = await self._query(PositionSnapshot.all()).order_by("-created_at").limit(limit)
        result: list[dict[str, object]] = []
        for row in rows:
            created_at: datetime | None = row.created_at
            result.append(
                {
                    "snapshot_key": row.snapshot_key,
                    "payload": row.payload,
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        return result

    async def list_since(self, cutoff: datetime, *, limit: int = 5000) -> list[dict[str, object]]:
        rows = (
            await self._query(PositionSnapshot.filter(created_at__gte=cutoff))
            .order_by("-created_at")
            .limit(limit)
        )
        result: list[dict[str, object]] = []
        for row in rows:
            created_at: datetime | None = row.created_at
            result.append(
                {
                    "snapshot_key": row.snapshot_key,
                    "payload": row.payload,
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        return result
