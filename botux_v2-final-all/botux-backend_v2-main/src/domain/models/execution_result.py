from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field

from domain.enums import ExecutionStatus


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    status: ExecutionStatus
    error_reason: str | None = None
    broker_order_id: str | None = None
    filled_qty: float = Field(ge=0)
    avg_price: float | None = Field(default=None, ge=0)
    schema_version: str = "v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
