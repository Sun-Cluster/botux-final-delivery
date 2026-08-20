from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from tortoise.backends.base.client import BaseDBAsyncClient

from db.models import OutboxEvent
from domain.enums import OutboxStatus

JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_event_key(prefix: str, entity_key: str) -> str:
    return f"{prefix}:{entity_key}:{uuid4().hex}"


async def append_outbox_event(
    *,
    event_type: str,
    entity_key: str,
    payload: dict[str, JSONValue],
    connection: BaseDBAsyncClient | None,
) -> None:
    event = OutboxEvent(
        event_key=make_event_key(event_type, entity_key),
        event_type=event_type,
        payload=payload,
        status=OutboxStatus.PENDING.value,
    )
    if connection is None:
        await event.save()
        return
    await event.save(using_db=connection)
