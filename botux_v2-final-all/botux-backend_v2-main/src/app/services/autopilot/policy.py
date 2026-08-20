from __future__ import annotations

from dataclasses import dataclass

from db.repositories.autopilot_repo import AutopilotRepository
from db.repositories._common import JSONValue
from db.uow import UnitOfWork

DEFAULT_POLICY_NAME = "fleet_autopilot_mvp"
SUPPORTED_MODES = {"observe", "recommend", "constrained_apply"}


@dataclass(frozen=True)
class PolicyValidationResult:
    valid: bool
    reason_codes: list[str]


class AutopilotPolicyService:
    async def get_effective_policy(self) -> dict[str, JSONValue]:
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            policy = await repo.get_active_policy()
            if policy is not None:
                return normalize_policy(policy)
            created = await repo.upsert_policy(**default_policy_payload())
            return normalize_policy(created)

    async def update_policy(self, patch: dict[str, object]) -> dict[str, JSONValue]:
        current = await self.get_effective_policy()
        merged = {
            "name": str(patch.get("name", current["name"])).strip() or str(current["name"]),
            "enabled": bool(patch.get("enabled", current["enabled"])),
            "mode": _normalize_mode(patch.get("mode", current["mode"])),
            "evaluation_window_days": _coerce_int(
                patch.get("evaluation_window_days", current.get("evaluation_window_days", 7)),
                default=7,
                minimum=1,
            ),
            "shadow_min_closed_trades": _coerce_int(
                patch.get("shadow_min_closed_trades", current.get("shadow_min_closed_trades", 4)),
                default=4,
                minimum=1,
            ),
            "shadow_max_win_rate": _coerce_float(
                patch.get("shadow_max_win_rate", current.get("shadow_max_win_rate", 45.0)),
                default=45.0,
            ),
            "shadow_max_pnl_pct": _coerce_float(
                patch.get("shadow_max_pnl_pct", current.get("shadow_max_pnl_pct", -2.0)),
                default=-2.0,
            ),
            "reactivate_interval_seconds": _coerce_int(
                patch.get("reactivate_interval_seconds", current.get("reactivate_interval_seconds", 86400)),
                default=86400,
                minimum=0,
            ),
            "reactivate_min_closed_trades": _coerce_int(
                patch.get("reactivate_min_closed_trades", current.get("reactivate_min_closed_trades", 4)),
                default=4,
                minimum=1,
            ),
            "reactivate_min_win_rate": _coerce_float(
                patch.get("reactivate_min_win_rate", current.get("reactivate_min_win_rate", 55.0)),
                default=55.0,
            ),
            "reactivate_min_pnl_pct": _coerce_float(
                patch.get("reactivate_min_pnl_pct", current.get("reactivate_min_pnl_pct", 1.0)),
                default=1.0,
            ),
        }
        async with UnitOfWork() as uow:
            repo = AutopilotRepository(connection=uow.connection)
            updated = await repo.upsert_policy(**merged)
        return normalize_policy(updated)


def default_policy_payload() -> dict[str, object]:
    return {
        "name": DEFAULT_POLICY_NAME,
        "enabled": True,
        "mode": "observe",
        "evaluation_window_days": 7,
        "shadow_min_closed_trades": 4,
        "shadow_max_win_rate": 45.0,
        "shadow_max_pnl_pct": -2.0,
        "reactivate_interval_seconds": 86400,
        "reactivate_min_closed_trades": 4,
        "reactivate_min_win_rate": 55.0,
        "reactivate_min_pnl_pct": 1.0,
    }


def normalize_policy(policy: dict[str, JSONValue]) -> dict[str, JSONValue]:
    defaults = default_policy_payload()
    return {
        "id": policy.get("id"),
        "name": str(policy.get("name", defaults["name"])).strip() or str(defaults["name"]),
        "enabled": bool(policy.get("enabled", defaults["enabled"])),
        "mode": _normalize_mode(policy.get("mode")),
        "evaluation_window_days": _coerce_int(
            policy.get("evaluation_window_days"),
            default=int(defaults["evaluation_window_days"]),
            minimum=1,
        ),
        "shadow_min_closed_trades": _coerce_int(
            policy.get("shadow_min_closed_trades"),
            default=int(defaults["shadow_min_closed_trades"]),
            minimum=1,
        ),
        "shadow_max_win_rate": _coerce_float(
            policy.get("shadow_max_win_rate"),
            default=float(defaults["shadow_max_win_rate"]),
        ),
        "shadow_max_pnl_pct": _coerce_float(
            policy.get("shadow_max_pnl_pct"),
            default=float(defaults["shadow_max_pnl_pct"]),
        ),
        "reactivate_interval_seconds": _coerce_int(
            policy.get("reactivate_interval_seconds"),
            default=int(defaults["reactivate_interval_seconds"]),
            minimum=0,
        ),
        "reactivate_min_closed_trades": _coerce_int(
            policy.get("reactivate_min_closed_trades"),
            default=int(defaults["reactivate_min_closed_trades"]),
            minimum=1,
        ),
        "reactivate_min_win_rate": _coerce_float(
            policy.get("reactivate_min_win_rate"),
            default=float(defaults["reactivate_min_win_rate"]),
        ),
        "reactivate_min_pnl_pct": _coerce_float(
            policy.get("reactivate_min_pnl_pct"),
            default=float(defaults["reactivate_min_pnl_pct"]),
        ),
        "created_at": policy.get("created_at"),
        "updated_at": policy.get("updated_at"),
    }


def validate_policy(policy: dict[str, JSONValue]) -> PolicyValidationResult:
    normalized = normalize_policy(policy)
    reasons: list[str] = []
    if str(normalized.get("mode", "observe")) not in SUPPORTED_MODES:
        reasons.append("invalid_mode")
    if int(normalized.get("evaluation_window_days", 0) or 0) <= 0:
        reasons.append("invalid_evaluation_window_days")
    if int(normalized.get("shadow_min_closed_trades", 0) or 0) <= 0:
        reasons.append("invalid_shadow_thresholds")
    if int(normalized.get("reactivate_min_closed_trades", 0) or 0) <= 0:
        reasons.append("invalid_reactivate_thresholds")
    return PolicyValidationResult(valid=not reasons, reason_codes=sorted(set(reasons)))


def _normalize_mode(value: object) -> str:
    mode = str(value or "observe").strip().lower()
    if mode not in SUPPORTED_MODES:
        return "observe"
    return mode


def _coerce_int(value: object, *, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _coerce_float(value: object, *, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
