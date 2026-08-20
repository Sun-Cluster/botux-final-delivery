from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from db.repositories._common import JSONValue

_LANE_ALIASES: dict[str, str] = {
    "copycat": "tradecopy",
    "tradecopy": "tradecopy",
    "gambler": "options",
    "options": "options",
    "drifter": "swingtrade",
    "swingtrade": "swingtrade",
    "nugget": "ausmine",
    "ausmining": "ausmine",
    "miner": "ausmine",
    "evo": "evo_catalyst",
    "evo_catalyst": "evo_catalyst",
    "evo-catalyst": "evo_catalyst",
    "volt": "evo_catalyst",
}


def scaled_metric(symbol: str, salt: str, min_value: float, max_value: float) -> float:
    raw = hashlib.md5(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    normalized = int(raw[:8], 16) / 0xFFFFFFFF
    return min_value + ((max_value - min_value) * normalized)


def payload_to_object_dict(payload: dict[str, JSONValue]) -> dict[str, object]:
    return {str(key): value for key, value in payload.items()}


def json_payload(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): json_value(item) for key, item in value.items()}


def json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


def as_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def canonical_lane(lane: str) -> str:
    return _LANE_ALIASES.get(lane.strip().lower(), lane.strip().lower())


def lane_source(canonical_lane: str) -> str:
    if canonical_lane == "ausmine":
        return "ausmine"
    return canonical_lane


def source_from_signal_id(signal_id: str) -> str:
    prefix = signal_id.split(":", 1)[0].lower()
    if prefix in {"tradecopy", "options", "swingtrade", "ausmine", "evo_catalyst"}:
        return prefix
    if prefix == "news":
        return "alpaca_news"
    if prefix == "scout":
        return "scout"
    return prefix


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
