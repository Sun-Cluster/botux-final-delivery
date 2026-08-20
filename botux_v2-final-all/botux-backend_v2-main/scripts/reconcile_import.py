from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


async def _run() -> None:
    root = Path(__file__).resolve().parents[1]
    staging_dir = root / "docs" / "context" / "artifacts" / "import_staging"
    runtime_report = staging_dir / "runtime_files_staging_report.json"
    supabase_report = staging_dir / "supabase_staging_report.json"
    if not runtime_report.exists() and not supabase_report.exists():
        raise SystemExit("No staging reports found. Run import scripts first.")

    runtime_rows = _load_rows(runtime_report)
    supabase_rows = _load_rows(supabase_report)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_sources": len(runtime_rows),
        "supabase_sources": len(supabase_rows),
        "runtime_rows_total": sum(int(item.get("estimated_rows", 0)) for item in runtime_rows),
        "supabase_rows_total": sum(int(item.get("estimated_rows", 0)) for item in supabase_rows),
    }

    payload = {
        "summary": summary,
        "runtime_rows": runtime_rows,
        "supabase_rows": supabase_rows,
    }
    json_path = staging_dir / "reconcile_report.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_path = root / "docs" / "context" / "RECONCILE_IMPORT_REPORT.md"
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"Wrote reconcile report: {json_path}")
    print(f"Wrote reconcile markdown: {md_path}")


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    return []


def _to_markdown(payload: dict) -> str:
    summary = payload["summary"]
    lines: list[str] = []
    lines.append("# Import Reconciliation Report")
    lines.append("")
    lines.append(f"Generated at: {summary['generated_at_utc']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Runtime sources: {summary['runtime_sources']}")
    lines.append(f"- Supabase sources: {summary['supabase_sources']}")
    lines.append(f"- Runtime estimated rows: {summary['runtime_rows_total']}")
    lines.append(f"- Supabase estimated rows: {summary['supabase_rows_total']}")
    lines.append("")
    lines.append("## Runtime Rows")
    lines.append("")
    for row in payload["runtime_rows"]:
        lines.append(
            f"- `{row.get('source_rel', 'n/a')}` -> `{row.get('target_table_hint', 'n/a')}`: {row.get('estimated_rows', 0)}"
        )
    lines.append("")
    lines.append("## Supabase Rows")
    lines.append("")
    for row in payload["supabase_rows"]:
        lines.append(
            f"- `{row.get('source_table', 'n/a')}`: {row.get('estimated_rows', 0)}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
