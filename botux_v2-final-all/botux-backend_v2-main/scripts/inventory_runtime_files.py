from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SKIP_PREFIXES = {"_archive", "archive"}

CRITICAL_FILES = {
    "bot_profiles.json",
    "strategy_registry.json",
    "promotion_readiness.jsonl",
    "strategy_shadow_metrics.jsonl",
    "gate_failures.jsonl",
    "event_outcomes.json",
}

TARGET_HINTS = {
    "bot_profiles.json": "bot_profiles",
    "strategy_registry.json": "strategy_registry",
    "promotion_readiness.jsonl": "audit_logs",
    "strategy_shadow_metrics.jsonl": "audit_logs",
    "gate_failures.jsonl": "gate_failures",
    "event_outcomes.json": "trade_outcomes",
}


@dataclass(frozen=True)
class RuntimeFileInfo:
    rel_path: str
    ext: str
    size_bytes: int
    line_count: int
    classification: str
    target_hint: str | None


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    reference_data = root.parent / "botux-backend" / "data"
    if not reference_data.exists():
        raise SystemExit(f"Reference data directory not found: {reference_data}")

    infos: list[RuntimeFileInfo] = []
    for path in sorted(reference_data.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".jsonl"}:
            continue
        rel = path.relative_to(reference_data).as_posix()
        if any(part in SKIP_PREFIXES for part in path.parts):
            classification = "archival"
        else:
            classification = _classify(path.name)
        infos.append(
            RuntimeFileInfo(
                rel_path=rel,
                ext=path.suffix.lower(),
                size_bytes=path.stat().st_size,
                line_count=_count_lines(path),
                classification=classification,
                target_hint=TARGET_HINTS.get(path.name),
            )
        )

    class_counts: Counter[str] = Counter(item.classification for item in infos)
    artifact = {
        "snapshot_date": str(date.today()),
        "reference_data_dir": str(reference_data),
        "total_files": len(infos),
        "classification_counts": dict(class_counts.most_common()),
        "files": [
            {
                "rel_path": item.rel_path,
                "ext": item.ext,
                "size_bytes": item.size_bytes,
                "line_count": item.line_count,
                "classification": item.classification,
                "target_hint": item.target_hint,
            }
            for item in infos
        ],
    }

    context_dir = root / "docs" / "context"
    artifact_dir = context_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    json_path = artifact_dir / "runtime_file_inventory.json"
    json_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    md_path = context_dir / "RUNTIME_FILE_INVENTORY.md"
    md_path.write_text(_to_markdown(artifact), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    except (UnicodeDecodeError, OSError):
        return 0


def _classify(name: str) -> str:
    if name in CRITICAL_FILES:
        return "state_critical"
    if name.endswith(".jsonl"):
        return "event_stream"
    return "reference_or_config"


def _to_markdown(artifact: dict) -> str:
    lines: list[str] = []
    lines.append("# Runtime File Inventory")
    lines.append("")
    lines.append(f"Snapshot date: {artifact['snapshot_date']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total JSON/JSONL files: {artifact['total_files']}")
    for classification, count in artifact["classification_counts"].items():
        lines.append(f"- `{classification}`: {count}")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    for item in artifact["files"]:
        target = item["target_hint"] if item["target_hint"] else "n/a"
        lines.append(
            f"- `{item['rel_path']}` ({item['classification']}) - {item['size_bytes']} bytes, {item['line_count']} lines, target hint: `{target}`"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
