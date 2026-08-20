from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from api.deps import get_container
from app.services.measurement.service import MeasurementService
from db.repositories.bots_repo import BotsRepository
from db.repositories._common import JSONValue
from db.uow import UnitOfWork
from runtime.container import Container

router = APIRouter(prefix="/api", tags=["bot-registry"])

ALLOWED_LIFECYCLE: set[str] = {
    "uploaded",
    "shadow",
    "paper",
    "live",
    "scaled",
    "demoted",
    "offline",
    "retired",
    "testing",
    "strong_paper",
    "tweak",
    "seeded",
    "arena",
    "archived",
}


class BotProfilePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    mission: str | None = None
    strategy_type: str | None = None
    horizon: str | None = None
    market: str | None = None
    broker: str | None = None
    mode: str | None = None
    lifecycle_state: str | None = None
    status: str | None = None
    icon: str | None = None
    intel_source: str | None = None
    notes: str | None = None
    enabled: bool | None = None
    autopilot_state: str | None = None
    autopilot_changed_at: str | None = None
    allocation: dict[str, object] | None = None
    risk: dict[str, object] | None = None
    order_types: list[str] | None = None
    allowed_brokers: list[str] | None = None


class StrategyPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    version: str | None = None
    description: str | None = None
    lifecycle_state: str | None = None
    bot_ids: list[str] | None = None
    market: str | None = None
    broker: str | None = None
    signal_sources: list[str] | None = None
    min_score_threshold: float | None = None
    min_confidence: float | None = None
    metrics_window: str | None = None
    intel_engine: str | None = None
    notes: str | None = None


@router.get("/bots/canonical")
async def get_canonical_profiles(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_bot_profiles()
    scorecards = await _safe_scorecards_by_bot(limit=2000)
    scheduler_active = _scheduler_active(container)
    profiles = {
        bot_id: _dashboard_profile(
            bot_id,
            profile,
            scheduler_active=scheduler_active,
            scorecard=scorecards.get(_normalize_bot_id(bot_id)),
        )
        for bot_id, profile in profiles.items()
    }
    return {
        "schema_version": "v1",
        "lifecycle_states": sorted(ALLOWED_LIFECYCLE),
        "profiles": profiles,
        "count": len(profiles),
        "source": "db",
        "status_source": "enabled+lifecycle+scheduler",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/bots/canonical/{bot_id}")
async def get_canonical_profile(bot_id: str, container: Container = Depends(get_container)) -> dict[str, object]:
    profile = await _safe_get_bot_profile(bot_id)
    if profile is None:
        profiles = await _safe_list_bot_profiles()
        return {"error": f"bot_id '{bot_id}' not found", "available": list(profiles.keys())}
    scorecards = await _safe_scorecards_by_bot(limit=2000)
    return {
        "profile": _dashboard_profile(
            bot_id,
            profile,
            scheduler_active=_scheduler_active(container),
            scorecard=scorecards.get(_normalize_bot_id(bot_id)),
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/bots/canonical/{bot_id}")
async def update_bot_profile(bot_id: str, updates: BotProfilePatchRequest) -> dict[str, object]:
    patch = updates.model_dump(exclude_none=True)
    if not patch:
        return {
            "updated": False,
            "bot_id": bot_id,
            "error": "no allowed fields in update payload",
        }
    if "lifecycle_state" in patch:
        lifecycle_state = str(patch["lifecycle_state"]).strip().lower()
        if lifecycle_state not in ALLOWED_LIFECYCLE:
            return {
                "updated": False,
                "bot_id": bot_id,
                "error": f"invalid lifecycle_state '{lifecycle_state}'",
                "allowed_lifecycle_states": sorted(ALLOWED_LIFECYCLE),
            }
        patch["lifecycle_state"] = lifecycle_state

    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            existing = await repo.get_bot_profile(bot_id)
            if existing is None:
                return {"updated": False, "bot_id": bot_id, "error": "bot profile not found"}
            merged = _merge_json_dict(existing, patch)
            await repo.upsert_bot_profile(bot_id, merged)
    except Exception as exc:
        return {"updated": False, "bot_id": bot_id, "error": str(exc)[:200]}

    return {
        "updated": True,
        "bot_id": bot_id,
        "patch": patch,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/bot/profiles")
async def get_bot_profiles(container: Container = Depends(get_container)) -> dict[str, list[dict[str, object]]]:
    profiles = await _safe_list_bot_profiles()
    scorecards = await _safe_scorecards_by_bot(limit=2000)
    scheduler_active = _scheduler_active(container)
    rows: list[dict[str, object]] = []
    for bot_id, profile in profiles.items():
        rows.append(
            {
                "canonical_id": bot_id,
                **_dashboard_profile(
                    bot_id,
                    profile,
                    scheduler_active=scheduler_active,
                    scorecard=scorecards.get(_normalize_bot_id(bot_id)),
                ),
            }
        )
    rows.sort(key=lambda item: str(item.get("bot_id", "")))
    return {"profiles": rows}


@router.get("/strategies")
async def get_strategy_registry() -> dict[str, object]:
    strategies = await _safe_list_strategies()
    return {
        "schema_version": "v1",
        "lifecycle": {},
        "strategies": strategies,
        "count": len(strategies),
        "source": "db",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str) -> dict[str, object]:
    strategy = await _safe_get_strategy(strategy_id)
    if strategy is None:
        strategies = await _safe_list_strategies()
        return {"error": f"strategy_id '{strategy_id}' not found", "available": list(strategies.keys())}
    return {"strategy": strategy, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.patch("/strategies/{strategy_id}")
async def update_strategy(strategy_id: str, updates: StrategyPatchRequest) -> dict[str, object]:
    patch = updates.model_dump(exclude_none=True)
    if not patch:
        return {
            "updated": False,
            "strategy_id": strategy_id,
            "error": "no allowed fields in update payload",
        }
    if "lifecycle_state" in patch:
        lifecycle_state = str(patch["lifecycle_state"]).strip().lower()
        if lifecycle_state not in ALLOWED_LIFECYCLE:
            return {
                "updated": False,
                "strategy_id": strategy_id,
                "error": f"invalid lifecycle_state '{lifecycle_state}'",
                "allowed_lifecycle_states": sorted(ALLOWED_LIFECYCLE),
            }
        patch["lifecycle_state"] = lifecycle_state

    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            existing = await repo.get_strategy_registry(strategy_id)
            if existing is None:
                return {"updated": False, "strategy_id": strategy_id, "error": "strategy not found"}
            merged = _merge_json_dict(existing, patch)
            await repo.upsert_strategy_registry(strategy_id, merged)
    except Exception as exc:
        return {"updated": False, "strategy_id": strategy_id, "error": str(exc)[:200]}

    return {
        "updated": True,
        "strategy_id": strategy_id,
        "patch": patch,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _safe_list_bot_profiles() -> dict[str, dict]:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.list_bot_profiles()
    except Exception:
        return {}


async def _safe_get_bot_profile(bot_id: str) -> dict | None:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.get_bot_profile(bot_id)
    except Exception:
        return None


async def _safe_list_strategies() -> dict[str, dict]:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.list_strategy_registry()
    except Exception:
        return {}


async def _safe_get_strategy(strategy_id: str) -> dict | None:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.get_strategy_registry(strategy_id)
    except Exception:
        return None


async def _safe_scorecards_by_bot(limit: int) -> dict[str, dict]:
    try:
        return await MeasurementService().scorecards_by_bot(limit=limit)
    except Exception:
        return {}


def _merge_json_dict(base: dict, patch: dict[str, object]) -> dict[str, JSONValue]:
    merged: dict[str, JSONValue] = {
        key: value
        for key, value in base.items()
        if isinstance(key, str) and _is_json_value(value)
    }
    for key, value in patch.items():
        if _is_json_value(value):
            merged[key] = cast(JSONValue, value)
    return merged


def _dashboard_profile(
    bot_id: str,
    profile: dict,
    *,
    scheduler_active: bool,
    scorecard: dict | None = None,
) -> dict[str, object]:
    lifecycle = str(profile.get("lifecycle_state") or "unknown").strip().lower()
    enabled = bool(profile.get("enabled", False))
    autopilot_state = str(profile.get("autopilot_state") or "active").strip().lower() or "active"
    stored_status = str(profile.get("status") or "").strip().lower() or None
    runtime_status = _runtime_status(
        lifecycle=lifecycle,
        enabled=enabled,
        scheduler_active=scheduler_active,
    )
    row: dict[str, object] = {
        **profile,
        "bot_id": bot_id,
        "stored_status": stored_status,
        "runtime_status": runtime_status,
        "status": runtime_status,
        "scheduler_active": scheduler_active,
        "lifecycle_state": lifecycle.upper(),
        "lifecycle_state_raw": lifecycle,
        "autopilot_state": autopilot_state,
        "broker_submit_allowed": enabled and lifecycle in {"paper", "live", "scaled"} and autopilot_state != "shadow",
    }
    profile_performance = profile.get("performance")
    existing_performance = profile_performance if isinstance(profile_performance, dict) else {}
    row["performance"] = _merge_performance(existing=existing_performance, scorecard=scorecard)
    return row


def _runtime_status(*, lifecycle: str, enabled: bool, scheduler_active: bool) -> str:
    if lifecycle in {"offline", "retired", "demoted"}:
        return "offline"
    if not enabled:
        return "inactive"
    if scheduler_active:
        return "active"
    if lifecycle in {"paper", "live", "scaled", "shadow"}:
        return "idle"
    return "idle"


def _scheduler_active(container: Container) -> bool:
    if container.scheduler is None:
        return False
    snapshot = container.scheduler.snapshot()
    return bool(snapshot.get("enabled")) and bool(snapshot.get("active")) and int(snapshot.get("job_count", 0)) > 0


def _empty_performance() -> dict[str, object]:
    return {
        "total_trades": 0,
        "closed_trades": 0,
        "open_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_r": None,
        "total_pnl": None,
        "gross_profit": None,
        "gross_loss": None,
        "sharpe": None,
        "quality_score": None,
        "confidence": None,
        "suppressed": False,
    }


def _merge_performance(*, existing: dict, scorecard: dict | None) -> dict[str, object]:
    base = _empty_performance()
    for key in base:
        if key in existing:
            base[key] = existing[key]
    if not scorecard:
        return base

    wins = _as_int(scorecard.get("wins"))
    losses = _as_int(scorecard.get("losses"))
    closed_trades = _as_int(scorecard.get("closed_trades", scorecard.get("total_trades")))
    open_trades = _as_int(existing.get("open_trades"))
    gross_profit = _as_float_or_none(scorecard.get("gross_profit"))
    gross_loss = _as_float_or_none(scorecard.get("gross_loss"))
    total_pnl = None
    if gross_profit is not None or gross_loss is not None:
        total_pnl = round((gross_profit or 0.0) - (gross_loss or 0.0), 4)

    base.update(
        {
            "total_trades": _as_int(scorecard.get("total_trades", closed_trades)),
            "closed_trades": closed_trades,
            "open_trades": open_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": _as_float_or_none(scorecard.get("win_rate")),
            "avg_r": _as_float_or_none(scorecard.get("expectancy_r")),
            "total_pnl": total_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "sharpe": _as_float_or_none(scorecard.get("sharpe")),
            "quality_score": _as_float_or_none(scorecard.get("quality_score")),
            "confidence": scorecard.get("confidence"),
            "suppressed": bool(scorecard.get("suppressed", False)),
        }
    )
    return base


def _normalize_bot_id(bot_id: str) -> str:
    normalized = bot_id.strip().lower()
    return {
        "swingtrade": "drifter",
        "options": "gambler",
        "tradecopy": "copycat",
        "ausmining": "nugget_bot",
        "nugget": "nugget_bot",
    }.get(normalized, normalized)


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_json_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False
