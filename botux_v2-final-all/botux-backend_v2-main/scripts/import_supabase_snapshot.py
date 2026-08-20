from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

TABLES = [
    "signals",
    "council_decisions",
    "trade_outcomes",
    "bot_profiles",
    "strategy_registry",
]


async def _run() -> None:
    root = Path(__file__).resolve().parents[1]
    export_path = root / "docs" / "context" / "artifacts" / "supabase_export" / "supabase_snapshot.json"
    if not export_path.exists():
        raise SystemExit(
            f"Supabase export not found: {export_path}. "
            "Place a JSON snapshot there to run staging import report."
        )

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for table in TABLES:
        raw = payload.get(table, [])
        if isinstance(raw, list):
            count = len(raw)
        else:
            count = 0
        rows.append(
            {
                "source_table": table,
                "estimated_rows": count,
                "import_mode": "dry_run",
            }
        )

    output_dir = root / "docs" / "context" / "artifacts" / "import_staging"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "supabase_staging_report.json"
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_export_path": str(export_path),
        "rows": rows,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote supabase staging report: {report_path}")
    print(f"Tables: {len(rows)}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
