from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.queue.bus import InProcessQueueBus
from runtime.health import event_loop_latency_ms


async def _run() -> None:
    root = ROOT
    bus = InProcessQueueBus()
    payload = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "queue_depth": bus.snapshot_sizes(),
        "event_loop_latency_ms": await event_loop_latency_ms(),
        "error_rate_baseline": {
            "source": "no_live_telemetry_in_workspace",
            "value": 0.0,
            "unit": "errors_per_minute",
        },
        "retry_rate_baseline": {
            "source": "no_live_telemetry_in_workspace",
            "value": 0.0,
            "unit": "retries_per_minute",
        },
    }
    out_dir = root / "docs" / "context" / "artifacts" / "stabilization"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "baseline_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote stabilization baseline: {out_path}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
