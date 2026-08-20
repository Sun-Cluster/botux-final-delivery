from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    checklist = root / "docs" / "checklists" / "ROLLBACK_CHECKLIST.md"
    if not checklist.exists():
        raise SystemExit(f"Missing rollback checklist: {checklist}")

    lines = checklist.read_text(encoding="utf-8").splitlines()
    items = [line for line in lines if line.strip().startswith("- [")]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "checklist_path": checklist.as_posix(),
        "total_items": len(items),
        "status": "dry_run_ready",
        "items": items,
        "notes": [
            "Dry-run validates checklist readability and report generation.",
            "Execution actions are manual and operator-controlled.",
        ],
    }
    out_dir = root / "docs" / "context" / "artifacts" / "rollback"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "rollback_dry_run_report.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path = root / "docs" / "context" / "ROLLBACK_DRY_RUN_REPORT.md"
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"Wrote rollback dry-run report: {json_path}")
    print(f"Wrote rollback markdown report: {md_path}")


def _to_markdown(payload: dict) -> str:
    lines: list[str] = []
    lines.append("# Rollback Dry-Run Report")
    lines.append("")
    lines.append(f"Generated at: {payload['generated_at_utc']}")
    lines.append("")
    lines.append(f"Checklist path: `{payload['checklist_path']}`")
    lines.append(f"Total items: {payload['total_items']}")
    lines.append("")
    lines.append("## Items")
    lines.append("")
    for item in payload["items"]:
        lines.append(f"- {item[2:]}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for note in payload["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
