from __future__ import annotations

from typing import Any, cast

from db.models import BotProfile, StrategyRegistry
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase

_PROFILE_SCALAR_KEYS = {
    "display_name",
    "mission",
    "strategy_type",
    "horizon",
    "market",
    "broker",
    "mode",
    "lifecycle_state",
    "status",
    "icon",
    "intel_source",
    "notes",
    "enabled",
    "autopilot_state",
    "autopilot_changed_at",
}
_PROFILE_COMPOSITE_KEYS = {"allocation", "risk", "order_types", "allowed_brokers", "compat_aliases"}
_PROFILE_KNOWN_KEYS = _PROFILE_SCALAR_KEYS | _PROFILE_COMPOSITE_KEYS


class BotsRepository(RepositoryBase):
    async def get_bot_profile(self, bot_id: str) -> dict | None:
        row = await self._query(BotProfile.filter(bot_id=bot_id)).first()
        if row is None:
            return None
        return _row_to_profile(row)

    async def upsert_bot_profile(self, bot_id: str, profile: dict[str, JSONValue]) -> None:
        row = await self._query(BotProfile.filter(bot_id=bot_id)).first()
        if row is None:
            row = BotProfile(bot_id=bot_id)
        _apply_profile(row, profile)
        await self._save(row)
        await append_outbox_event(
            event_type="BotProfileUpdated",
            entity_key=bot_id,
            payload={"bot_id": bot_id, "profile": _row_to_profile(row)},
            connection=self._connection,
        )

    async def get_strategy_registry(self, strategy_id: str) -> dict | None:
        row = await self._query(StrategyRegistry.filter(strategy_id=strategy_id)).first()
        if row is None:
            return None
        return row.metadata

    async def upsert_strategy_registry(self, strategy_id: str, metadata: dict[str, JSONValue]) -> None:
        row = await self._query(StrategyRegistry.filter(strategy_id=strategy_id)).first()
        if row is None:
            row = StrategyRegistry(strategy_id=strategy_id, metadata=metadata)
        else:
            row.metadata = metadata
        await self._save(row)
        await append_outbox_event(
            event_type="StrategyRegistryUpdated",
            entity_key=strategy_id,
            payload={"strategy_id": strategy_id, "metadata": metadata},
            connection=self._connection,
        )

    async def list_bot_profiles(self) -> dict[str, dict]:
        rows = await self._query(BotProfile.all()).order_by("bot_id")
        result: dict[str, dict] = {}
        for row in rows:
            result[row.bot_id] = _row_to_profile(row)
        return result

    async def list_strategy_registry(self) -> dict[str, dict]:
        rows = await self._query(StrategyRegistry.all()).order_by("strategy_id")
        result: dict[str, dict] = {}
        for row in rows:
            result[row.strategy_id] = row.metadata
        return result


def _row_to_profile(row: BotProfile) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = dict(_json_dict(row.metadata))
    payload.update(
        {
        "display_name": row.display_name,
        "mission": row.mission,
        "strategy_type": row.strategy_type,
        "horizon": row.horizon,
        "market": row.market,
        "broker": row.broker,
        "mode": row.mode,
        "lifecycle_state": row.lifecycle_state,
        "status": row.status,
        "icon": row.icon,
        "intel_source": row.intel_source,
        "notes": row.notes,
        "enabled": row.enabled,
        "autopilot_state": row.autopilot_state,
        "autopilot_changed_at": None if row.autopilot_changed_at is None else row.autopilot_changed_at.isoformat(),
        }
    )
    return _compact(payload)


def _apply_profile(row: BotProfile, profile: dict[str, JSONValue]) -> None:
    row.display_name = _optional_text(profile.get("display_name"))
    row.mission = cast(Any, _optional_text(profile.get("mission")))
    row.strategy_type = _optional_text(profile.get("strategy_type"))
    row.horizon = _optional_text(profile.get("horizon"))
    row.market = _optional_text(profile.get("market"))
    row.broker = _optional_text(profile.get("broker"))
    row.mode = _optional_text(profile.get("mode"))
    row.lifecycle_state = _optional_text(profile.get("lifecycle_state"))
    row.status = _optional_text(profile.get("status"))
    row.icon = _optional_text(profile.get("icon"))
    row.intel_source = _optional_text(profile.get("intel_source"))
    row.notes = cast(Any, _optional_text(profile.get("notes")))
    row.enabled = _optional_bool(profile.get("enabled"), default=row.enabled)
    row.autopilot_state = _normalized_autopilot_state(profile.get("autopilot_state"), default=row.autopilot_state)
    row.autopilot_changed_at = _optional_datetime(profile.get("autopilot_changed_at"))
    row.metadata = _extract_metadata(profile)


def _dict(value: JSONValue | None) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _json_dict(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, JSONValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if _is_json_value(item):
            payload[key] = cast(JSONValue, item)
    return payload


def _optional_text(value: JSONValue | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: JSONValue | None, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _normalized_autopilot_state(value: JSONValue | None, *, default: str | None) -> str:
    text = _optional_text(value)
    if text in {"active", "shadow"}:
        return text
    fallback = _optional_text(default)
    if fallback in {"active", "shadow"}:
        return fallback
    return "active"


def _optional_datetime(value: JSONValue | None):
    if value is None:
        return None
    if hasattr(value, "isoformat") and hasattr(value, "tzinfo"):
        return value
    text = _optional_text(value)
    if text is None:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compact(payload: dict[str, JSONValue]) -> dict[str, JSONValue]:
    return {key: value for key, value in payload.items() if value is not None}


def _extract_metadata(profile: dict[str, JSONValue]) -> dict[str, JSONValue]:
    metadata: dict[str, JSONValue] = {}
    for key, value in profile.items():
        if key in _PROFILE_KNOWN_KEYS:
            continue
        if _is_json_value(value):
            metadata[key] = cast(JSONValue, value)
    allocation = _dict(profile.get("allocation"))
    if allocation:
        metadata["allocation"] = cast(JSONValue, allocation)
    risk = _dict(profile.get("risk"))
    if risk:
        metadata["risk"] = cast(JSONValue, risk)
    return metadata


def _is_json_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
