from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from domain.enums import TradeOutcomeStatus


class TradeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_id: str
    signal_id: str
    symbol: str
    outcome: TradeOutcomeStatus
    pnl_pct: float | None = None
    schema_version: str = "v1"
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    entry_price: float | None = Field(default=None, ge=0)
    exit_price: float | None = Field(default=None, ge=0)
    quantity: float | None = Field(default=None, ge=0)
    close_reason: str | None = None
    bot_id: str | None = None
    source: str | None = None
    action: str | None = None
    broker_order_id: str | None = None
    broker_name: str | None = None
    market: str | None = None
    order_type: str | None = None
    features: dict[str, JsonValue] = Field(default_factory=dict)
