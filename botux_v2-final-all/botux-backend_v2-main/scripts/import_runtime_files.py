from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

STATE_CRITICAL_FILES = {
    "bot_profiles.json": "bot_profiles",
    "strategy_registry.json": "strategy_registry",
    "gate_failures.jsonl": "gate_failures",
    "event_outcomes.json": "trade_outcomes",
    "promotion_readiness.jsonl": "audit_logs",
    "strategy_shadow_metrics.jsonl": "audit_logs",
}


async def _run() -> None:
    root = Path(__file__).resolve().parents[1]
    snapshots_root = root / "docs" / "context" / "artifacts" / "snapshots"
    if not snapshots_root.exists():
        raise SystemExit(
            "No snapshots found. Run scripts/snapshot_reference_sources.py first."
        )

    latest_snapshot = max([path for path in snapshots_root.iterdir() if path.is_dir()], key=lambda item: item.name)
    manifest_path = latest_snapshot / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"Snapshot manifest missing: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_rows: list[dict] = []
    for entry in manifest.get("entries", []):
        rel = entry.get("source_rel", "")
        target_path = latest_snapshot / entry.get("target_rel", "")
        if not target_path.exists():
            continue
        table_hint = STATE_CRITICAL_FILES.get(Path(rel).name, "unknown")
        row_count = await _estimate_rows(target_path)
        report_rows.append(
            {
                "source_rel": rel,
                "target_table_hint": table_hint,
                "size_bytes": entry.get("size_bytes", 0),
                "sha256": entry.get("sha256", ""),
                "estimated_rows": row_count,
                "import_mode": "dry_run",
            }
        )

    output_dir = root / "docs" / "context" / "artifacts" / "import_staging"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "runtime_files_staging_report.json"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": manifest.get("snapshot_id"),
        "rows": report_rows,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote runtime import staging report: {report_path}")
    print(f"Rows: {len(report_rows)}")


async def _estimate_rows(path: Path) -> int:
    # Keep async entrypoint style consistent with other scripts.
    if path.suffix.lower() == ".jsonl":
        return sum(1 for _ in path.open("r", encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if "profiles" in data and isinstance(data["profiles"], dict):
            return len(data["profiles"])
        if "strategies" in data and isinstance(data["strategies"], dict):
            return len(data["strategies"])
        return len(data)
    return 0


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
