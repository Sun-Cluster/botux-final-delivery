from __future__ import annotations

from typing import cast

from db.models import ExecutionRecord, OrderRecord
from db.repositories._common import JSONValue


def merge_json(raw: dict) -> dict[str, JSONValue]:
    merged: dict[str, JSONValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if is_json_value(value):
            merged[key] = value
    return merged


def is_json_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and is_json_value(v) for k, v in value.items())
    return False


def json_value(value: object) -> JSONValue:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        payload: dict[str, JSONValue] = {}
        for key, item in value.items():
            if isinstance(key, str):
                payload[key] = json_value(item)
        return payload
    return str(value)


def json_payload(value: dict[str, object]) -> dict[str, JSONValue]:
    return {key: json_value(item) for key, item in value.items()}


def as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def as_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0


def execution_order_id(row: ExecutionRecord) -> str | None:
    order = getattr(row, "order", None)
    order_pk = getattr(order, "id", None)
    if order_pk is None:
        return None
    return str(order_pk)


def order_signal_id(row: OrderRecord) -> int | None:
    signal = getattr(row, "signal", None)
    signal_pk = getattr(signal, "id", None)
    if signal_pk is None:
        return None
    try:
        return int(signal_pk)
    except (TypeError, ValueError):
        return None
