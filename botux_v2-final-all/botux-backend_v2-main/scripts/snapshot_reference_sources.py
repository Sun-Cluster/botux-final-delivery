from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CRITICAL_RELATIVE_PATHS = [
    "data/bot_profiles.json",
    "data/strategy_registry.json",
    "data/promotion_readiness.jsonl",
    "data/strategy_shadow_metrics.jsonl",
    "data/gate_failures.jsonl",
    "data/event_outcomes.json",
]


@dataclass(frozen=True)
class SnapshotEntry:
    source_rel: str
    target_rel: str
    size_bytes: int
    sha256: str


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    reference_root = root.parent / "botux-backend"
    if not reference_root.exists():
        raise SystemExit(f"Reference repo not found: {reference_root}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = root / "docs" / "context" / "artifacts" / "snapshots" / stamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    entries: list[SnapshotEntry] = []
    for rel in CRITICAL_RELATIVE_PATHS:
        source = reference_root / rel
        if not source.exists():
            continue
        target = snapshot_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entries.append(
            SnapshotEntry(
                source_rel=rel,
                target_rel=target.relative_to(snapshot_dir).as_posix(),
                size_bytes=target.stat().st_size,
                sha256=_sha256_file(target),
            )
        )

    manifest = {
        "snapshot_id": stamp,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference_root": str(reference_root),
        "target_dir": str(snapshot_dir),
        "entries": [entry.__dict__ for entry in entries],
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote snapshot manifest: {manifest_path}")
    print(f"Captured files: {len(entries)}")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    main()
