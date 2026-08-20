from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

TABLE_CALL_PATTERN = re.compile(r"\.table\(\s*['\"]([^'\"]+)['\"]\s*\)")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
SKIP_SUFFIXES = {".md", ".txt", ".html", ".css", ".js", ".map"}


@dataclass(frozen=True)
class FileStat:
    path: str
    flow: str
    call_count: int
    tables: list[str]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    reference_root = root.parent / "botux-backend"
    if not reference_root.exists():
        raise SystemExit(f"Reference repo not found: {reference_root}")

    file_stats: list[FileStat] = []
    table_counts: Counter[str] = Counter()
    flow_counts: Counter[str] = Counter()
    files_by_flow: dict[str, list[FileStat]] = defaultdict(list)

    for path in _iter_files(reference_root):
        text = _read_text(path)
        if text is None:
            continue
        matches = TABLE_CALL_PATTERN.findall(text)
        if not matches:
            continue
        rel = path.relative_to(reference_root).as_posix()
        flow = _infer_flow(rel)
        call_count = len(matches)
        stat = FileStat(
            path=rel,
            flow=flow,
            call_count=call_count,
            tables=sorted(set(matches)),
        )
        file_stats.append(stat)
        files_by_flow[flow].append(stat)
        flow_counts[flow] += call_count
        table_counts.update(matches)

    file_stats.sort(key=lambda item: item.call_count, reverse=True)
    artifact = {
        "snapshot_date": str(date.today()),
        "reference_root": str(reference_root),
        "total_files_with_calls": len(file_stats),
        "total_table_calls": sum(item.call_count for item in file_stats),
        "top_files": [
            {
                "path": item.path,
                "flow": item.flow,
                "call_count": item.call_count,
                "tables": item.tables,
            }
            for item in file_stats[:50]
        ],
        "table_counts": dict(table_counts.most_common()),
        "flow_counts": dict(flow_counts.most_common()),
        "files_by_flow": {
            flow: [
                {
                    "path": item.path,
                    "call_count": item.call_count,
                    "tables": item.tables,
                }
                for item in sorted(stats, key=lambda current: current.call_count, reverse=True)
            ]
            for flow, stats in sorted(files_by_flow.items(), key=lambda current: flow_counts[current[0]], reverse=True)
        },
    }

    context_dir = root / "docs" / "context"
    artifact_dir = context_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    json_path = artifact_dir / "supabase_calls_inventory.json"
    json_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    md_path = context_dir / "SUPABASE_CALL_INVENTORY.md"
    md_path.write_text(_to_markdown(artifact), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def _iter_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        paths.append(path)
    return paths


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _infer_flow(rel_path: str) -> str:
    if rel_path.startswith("routes/"):
        return "api_routes"
    if rel_path.startswith("bots/"):
        return "execution"
    if rel_path.startswith("runtime/"):
        return "runtime"
    if rel_path.startswith("agents/"):
        return "agents"
    if rel_path.startswith("ml/"):
        return "ml"
    if rel_path.startswith("core/"):
        return "core"
    if rel_path.startswith("scripts/"):
        return "scripts"
    return "other"


def _to_markdown(artifact: dict) -> str:
    top_files = artifact["top_files"]
    flow_counts = artifact["flow_counts"]
    table_counts = artifact["table_counts"]
    lines: list[str] = []
    lines.append("# Supabase Call Inventory")
    lines.append("")
    lines.append(f"Snapshot date: {artifact['snapshot_date']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Files with `.table(...)` calls: {artifact['total_files_with_calls']}")
    lines.append(f"- Total `.table(...)` calls: {artifact['total_table_calls']}")
    lines.append("")
    lines.append("## Top Files")
    lines.append("")
    for idx, item in enumerate(top_files[:20], start=1):
        lines.append(
            f"{idx}. `{item['path']}` ({item['flow']}) - {item['call_count']} calls - tables: {', '.join(item['tables'])}"
        )
    lines.append("")
    lines.append("## Flow Groups")
    lines.append("")
    for flow, count in flow_counts.items():
        lines.append(f"- `{flow}`: {count} calls")
    lines.append("")
    lines.append("## Table Frequency")
    lines.append("")
    for table, count in table_counts.items():
        lines.append(f"- `{table}`: {count}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
