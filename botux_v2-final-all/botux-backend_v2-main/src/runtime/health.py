from __future__ import annotations

import asyncio
from datetime import datetime, timezone


def runtime_health() -> dict:
    return {"status": "ok", "checked_at": datetime.now(timezone.utc).isoformat()}


async def event_loop_latency_ms(*, sample_seconds: float = 0.05) -> float:
    start = datetime.now(timezone.utc).timestamp()
    await asyncio.sleep(sample_seconds)
    end = datetime.now(timezone.utc).timestamp()
    elapsed = end - start
    lag = max(0.0, elapsed - sample_seconds)
    return lag * 1000.0
