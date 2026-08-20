from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from domain.enums import OrderAction


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    symbol: str
    action: OrderAction
    quantity: float = Field(gt=0)
    idempotency_key: str
    broker_name: str | None = None
    market: str | None = None
    order_type: str = "market"
    lane_hint: str | None = None
    strategy_hint: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: str = "v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def normalize(self) -> "OrderIntent":
        self.symbol = self.symbol.upper().strip()
        self.idempotency_key = self.idempotency_key.strip()
        self.broker_name = _normalized_optional_text(self.broker_name)
        if self.broker_name is not None:
            self.broker_name = self.broker_name.lower()
        self.market = _normalized_optional_text(self.market)
        if self.market is not None:
            self.market = self.market.lower()
        self.order_type = (_normalized_optional_text(self.order_type) or "market").lower()
        self.lane_hint = _normalized_optional_hint(self.lane_hint)
        self.strategy_hint = _normalized_optional_hint(self.strategy_hint)
        return self


def _normalized_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalized_optional_hint(value: str | None) -> str | None:
    normalized = _normalized_optional_text(value)
    if normalized is None:
        return None
    return normalized.lower()
