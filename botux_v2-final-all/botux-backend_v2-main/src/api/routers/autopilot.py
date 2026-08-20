from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from api.deps import get_container
from app.services.autopilot.policy import AutopilotPolicyService
from db.repositories.autopilot_repo import AutopilotRepository
from db.uow import UnitOfWork
from runtime.container import Container

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


class AutopilotPolicyPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    enabled: bool | None = None
    mode: str | None = None
    evaluation_window_days: int | None = None
    shadow_min_closed_trades: int | None = None
    shadow_max_win_rate: float | None = None
    shadow_max_pnl_pct: float | None = None
    reactivate_interval_seconds: int | None = None
    reactivate_min_closed_trades: int | None = None
    reactivate_min_win_rate: float | None = None
    reactivate_min_pnl_pct: float | None = None


@router.get("/status")
async def autopilot_status(container: Container = Depends(get_container)) -> dict[str, object]:
    policy_service = AutopilotPolicyService()
    policy = await policy_service.get_effective_policy()
    latest_run = None
    latest_decisions = []
    try:
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            latest_run = await repo.latest_run()
            latest_decisions = await repo.list_decisions(limit=200, run_id=_as_int(latest_run.get("id")) if isinstance(latest_run, dict) else None)
    except Exception:
        latest_run = None
        latest_decisions = []
    recommendation_counts = _recommendation_counts(latest_decisions)
    top_reasons = _top_reasons(latest_decisions)
    scheduler_snapshot = (
        container.scheduler.snapshot()
        if container.scheduler is not None
        else {"enabled": False, "active": False, "job_count": 0, "jobs": []}
    )
    autopilot_job = None
    jobs = scheduler_snapshot.get("jobs", [])
    if isinstance(jobs, list):
        for row in jobs:
            if isinstance(row, dict) and str(row.get("name")) == "autopilot.evaluate":
                autopilot_job = row
                break
    return {
        "policy": policy,
        "latest_run": latest_run,
        "latest_recommendation_counts": recommendation_counts,
        "top_reason_codes": top_reasons,
        "autopilot_job": autopilot_job,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/policy")
async def autopilot_policy() -> dict[str, object]:
    policy = await AutopilotPolicyService().get_effective_policy()
    return {
        "policy": policy,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.put("/policy")
async def autopilot_update_policy(request: AutopilotPolicyPatchRequest) -> dict[str, object]:
    policy = await AutopilotPolicyService().update_policy(request.model_dump(exclude_none=True))
    return {
        "policy": policy,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/runs")
async def autopilot_runs(limit: int = 20, before_id: int | None = None) -> dict[str, object]:
    safe_limit = max(1, min(limit, 100))
    rows: list[dict[str, object]]
    try:
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            rows = [dict(item) for item in await repo.list_runs(limit=safe_limit, before_id=before_id)]
    except Exception:
        rows = []
    return {
        "items": rows,
        "count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/runs/{run_id}")
async def autopilot_run_detail(run_id: int) -> dict[str, object]:
    try:
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            run = await repo.get_run(run_id)
            decisions = await repo.list_decisions(run_id=run_id, limit=500)
    except Exception:
        run = None
        decisions = []
    if run is None:
        return {"error": f"run_id '{run_id}' not found", "decisions": [], "generated_at": datetime.now(timezone.utc).isoformat()}
    return {
        "run": run,
        "decisions": decisions,
        "decision_count": len(decisions),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/decisions")
async def autopilot_decisions(
    limit: int = 50,
    run_id: int | None = None,
    bot_id: str | None = None,
    state: str | None = None,
) -> dict[str, object]:
    safe_limit = max(1, min(limit, 300))
    try:
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            rows = await repo.list_decisions(
                limit=safe_limit,
                run_id=run_id,
                bot_id=bot_id,
                recommended_state=state,
            )
    except Exception:
        rows = []
    return {
        "items": rows,
        "count": len(rows),
        "filters": {
            "run_id": run_id,
            "bot_id": bot_id,
            "state": state,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _recommendation_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("recommended_state", "unknown")).strip().lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _top_reasons(rows: list[dict[str, object]], *, limit: int = 8) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for row in rows:
        reason_codes = row.get("reason_codes")
        if not isinstance(reason_codes, list):
            continue
        for code in reason_codes:
            key = str(code).strip().lower()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"reason_code": key, "count": value} for key, value in ordered[:limit]]


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
