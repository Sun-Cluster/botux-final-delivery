from __future__ import annotations

import json

from db.models import SystemConfig
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase


class SystemConfigsRepository(RepositoryBase):
    async def get_by_key(self, key: str) -> dict[str, JSONValue] | None:
        row = await self._query(SystemConfig.filter(key=key)).first()
        if row is None:
            return None
        return _row_to_payload(row)

    async def list_all(self) -> list[dict[str, JSONValue]]:
        rows = await self._query(SystemConfig.all()).order_by("key")
        return [_row_to_payload(row) for row in rows]

    async def upsert(
        self,
        *,
        key: str,
        value: JSONValue,
        value_type: str,
        scope: str = "global",
        description: str | None = None,
        updated_by: str | None = None,
    ) -> dict[str, JSONValue]:
        row = await self._query(SystemConfig.filter(key=key)).first()
        if row is None:
            row = SystemConfig(key=key)
        row.value = _to_storage_value(value=value, value_type=value_type)
        row.value_type = value_type
        row.scope = scope
        row.description = description or ""
        row.updated_by = updated_by
        await self._save(row)
        payload = _row_to_payload(row)
        await append_outbox_event(
            event_type="SystemConfigUpdated",
            entity_key=key,
            payload={"config": payload},
            connection=self._connection,
        )
        return payload


def _row_to_payload(row: SystemConfig) -> dict[str, JSONValue]:
    return {
        "key": row.key,
        "value": _from_storage_value(value=row.value, value_type=row.value_type),
        "value_type": row.value_type,
        "scope": row.scope,
        "description": row.description,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _to_storage_value(*, value: JSONValue, value_type: str) -> JSONValue:
    if value_type == "str" and isinstance(value, str):
        return json.dumps(value)
    return value


def _from_storage_value(*, value: JSONValue, value_type: str) -> JSONValue:
    if value_type == "str" and isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value
        return decoded if isinstance(decoded, str) else value
    return value
