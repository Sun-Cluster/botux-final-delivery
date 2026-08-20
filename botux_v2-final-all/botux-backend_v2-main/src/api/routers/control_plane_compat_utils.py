from __future__ import annotations

from datetime import datetime, timezone


def object_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def object_dict_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
