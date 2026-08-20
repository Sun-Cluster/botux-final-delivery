from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from domain.enums import OrderAction, SignalStatus


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    symbol: str
    action: OrderAction
    score: float = Field(ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int = Field(default=5, ge=1, le=10)
    source: str = "unknown"
    headline: str | None = None
    lane_hint: str | None = None
    strategy_hint: str | None = None
    dedup_key: str | None = None
    scan_timestamp: datetime | None = None
    blocked_reason: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    status: SignalStatus = SignalStatus.PENDING
    schema_version: str = "v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def normalize(self) -> "Signal":
        self.symbol = self.symbol.upper().strip()
        self.source = (self.source or "unknown").strip().lower()
        self.headline = _normalized_optional_text(self.headline)
        self.lane_hint = _normalized_optional_hint(self.lane_hint)
        self.strategy_hint = _normalized_optional_hint(self.strategy_hint)
        self.blocked_reason = _normalized_optional_text(self.blocked_reason)
        if self.confidence is None:
            self.confidence = round(self.score, 4)
        if self.scan_timestamp is None:
            self.scan_timestamp = self.created_at
        if self.dedup_key is None:
            dedup_scope = self.strategy_hint or self.lane_hint or self.source
            self.dedup_key = f"{dedup_scope}:{self.symbol}:{self.action.value}"
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
