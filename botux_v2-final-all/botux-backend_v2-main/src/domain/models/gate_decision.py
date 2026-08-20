from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from domain.enums import CouncilDecision


class CouncilVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voter: str
    vote: str
    confidence: float = Field(ge=0.0, le=1.0)
    weight: float = Field(default=1.0, gt=0.0)
    reasoning: str
    veto: bool = False
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("vote")
    @classmethod
    def validate_vote(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"buy", "sell", "skip"}:
            raise ValueError("vote must be one of: buy, sell, skip")
        return normalized


class GateFailureDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_name: str
    reason: str
    veto: bool = False
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    decision: CouncilDecision
    reason: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    buy_votes: float = Field(default=0.0, ge=0.0)
    total_votes: int = Field(default=0, ge=0)
    vetoed: bool = False
    veto_reason: str | None = None
    votes: list[CouncilVote] = Field(default_factory=list)
    failures: list[GateFailureDetail] = Field(default_factory=list)
    approval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    position_size_pct: float | None = Field(default=None, ge=0.0)
    stop_loss_pct: float | None = Field(default=None, ge=0.0)
    take_profit_pct: float | None = Field(default=None, ge=0.0)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: str = "v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
