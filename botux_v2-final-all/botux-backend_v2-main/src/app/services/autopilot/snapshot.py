from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.services.autopilot.service import recent_window_start, summarize_outcomes
from app.services.control_plane.service import RuntimeControlPlaneService
from db.repositories._common import JSONValue
from db.repositories.bots_repo import BotsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork

if TYPE_CHECKING:
    from runtime.container import Container

EXECUTABLE_LIFECYCLES = {"paper", "live", "scaled"}


class AutopilotSnapshotService:
    async def build_snapshot(
        self,
        *,
        container: "Container",
        policy: dict[str, JSONValue],
    ) -> dict[str, JSONValue]:
        now = datetime.now(timezone.utc)
        control_plane = await RuntimeControlPlaneService().snapshot(container)
        bot_context = await self._bot_contexts(policy=policy, checked_at=now)
        return {
            "schema_version": "autopilot.snapshot.v2",
            "generated_at": now.isoformat(),
            "runtime_context": {
                "checked_at": control_plane.get("checked_at", now.isoformat()),
                "status": str(control_plane.get("status", "unknown")),
                "grade": str(control_plane.get("grade", "N/A")),
                "scheduler": _json_dict(control_plane.get("scheduler")),
            },
            "bot_performance_context": bot_context,
            "policy_context": _json_dict(policy),
        }

    async def _bot_contexts(
        self,
        *,
        policy: dict[str, JSONValue],
        checked_at: datetime,
    ) -> dict[str, JSONValue]:
        async with UnitOfWork() as uow:
            bots_repo = BotsRepository(connection=uow.connection)
            outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
            profiles = await bots_repo.list_bot_profiles()
            result: dict[str, JSONValue] = {}
            window_start = recent_window_start(
                now=checked_at,
                days=_as_int(policy.get("evaluation_window_days")),
            )
            for bot_id, profile in sorted(profiles.items()):
                enabled = bool(profile.get("enabled", False))
                lifecycle_state = str(profile.get("lifecycle_state", "unknown")).strip().lower()
                autopilot_state = _state(profile.get("autopilot_state"))
                if not enabled and autopilot_state != "shadow":
                    continue
                if lifecycle_state not in EXECUTABLE_LIFECYCLES and autopilot_state != "shadow":
                    continue
                changed_at = _as_datetime(profile.get("autopilot_changed_at"))
                recent_rows = await outcomes_repo.list_closed_rows_for_bot(
                    bot_id,
                    since=window_start,
                    limit=2000,
                )
                shadow_rows = await outcomes_repo.list_closed_rows_for_bot(
                    bot_id,
                    since=changed_at,
                    limit=2000,
                ) if changed_at is not None else []
                result[bot_id] = {
                    "bot_id": bot_id,
                    "enabled": enabled,
                    "lifecycle_state": lifecycle_state,
                    "autopilot_state": autopilot_state,
                    "autopilot_changed_at": None if changed_at is None else changed_at.isoformat(),
                    "recent_metrics": summarize_outcomes(rows=recent_rows, fallback_since=window_start),
                    "shadow_metrics": summarize_outcomes(rows=shadow_rows, fallback_since=changed_at),
                }
            return result


def _json_dict(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _state(value: object) -> str:
    normalized = str(value or "active").strip().lower()
    if normalized == "shadow":
        return "shadow"
    return "active"


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
