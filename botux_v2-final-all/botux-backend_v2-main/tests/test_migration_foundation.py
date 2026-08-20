from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


def test_tortoise_migration_foundation() -> None:
    if os.getenv("BOTUX_RUN_MIGRATION_TESTS", "0") != "1":
        pytest.skip("set BOTUX_RUN_MIGRATION_TESTS=1 to run migration integration tests")

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.setdefault("BOTUX_DB_URI", "postgres://botux:botux@127.0.0.1:5432/botux")
    proc = subprocess.run(
        ["uv", "run", "python", "scripts/verify_foundation.py"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (proc.stdout + "\n" + proc.stderr)[-4000:]

    report_file = root / "docs" / "context" / "artifacts" / "foundation" / "foundation_verification_report.json"
    assert report_file.exists()

    payload = json.loads(report_file.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    assert isinstance(summary, dict)
    assert summary.get("ok") is True
