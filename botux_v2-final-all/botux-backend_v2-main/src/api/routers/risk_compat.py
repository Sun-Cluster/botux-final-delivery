from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from api.deps import get_container
from runtime.container import Container

router = APIRouter(tags=["risk-compat"])


class HaltRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


@router.post("/risk/halt")
async def risk_halt(
    body: HaltRequest | None = None,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    container.trading_halted = True
    container.trading_halt_reason = body.reason if body is not None else None
    container.trading_halted_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "HALTED",
        "reason": container.trading_halt_reason,
        "halted_at": container.trading_halted_at,
    }


@router.post("/risk/resume")
async def risk_resume(container: Container = Depends(get_container)) -> dict[str, object]:
    previous_reason = container.trading_halt_reason
    container.trading_halted = False
    container.trading_halt_reason = None
    container.trading_halted_at = None
    return {"status": "ACTIVE", "resumed": True, "previous_reason": previous_reason}
