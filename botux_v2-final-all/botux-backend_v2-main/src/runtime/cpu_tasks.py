from __future__ import annotations

import os
from typing import cast

JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]


def preprocess_signal_payload(payload: dict[str, JSONValue]) -> dict[str, JSONValue]:
    score_raw = payload.get("score", 0.0)
    score = float(cast(float | int | str, score_raw))
    if score < 0:
        score = 0.0
    if score > 1:
        score = 1.0
    normalized_payload = dict(payload)
    normalized_payload["score"] = score
    return normalized_payload


def busy_sum(limit: int) -> int:
    total = 0
    for i in range(limit):
        total += (i % 7) * (i % 13)
    return total


def worker_pid() -> int:
    return os.getpid()
