from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class QueueEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str
    msg_type: str
    payload: dict[str, JsonValue]
    trace_id: str
    attempt: int = 0
    available_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
