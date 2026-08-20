#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs/context/artifacts/refactor_validation_matrix.json"
REPORT_DIR = ROOT / "docs/context/artifacts/refactor_validation"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run(command: str) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        shlex.split(command),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _git_changed_files() -> tuple[str, list[str]]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        compare = "HEAD (working tree)"
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        files = [line.strip() for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if untracked.returncode == 0:
            files.extend(line.strip() for line in untracked.stdout.splitlines() if line.strip())
        files = sorted(set(files))
        return compare, files

    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        compare = f"origin/{base_ref}...HEAD"
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", f"origin/{base_ref}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if verify.returncode == 0:
            out = subprocess.run(
                ["git", "diff", "--name-only", compare],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if out.returncode == 0:
                files = [line.strip() for line in out.stdout.splitlines() if line.strip()]
                return compare, files

    compare = "HEAD~1..HEAD"
    out = subprocess.run(
        ["git", "diff", "--name-only", compare],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    files = [line.strip() for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []
    return compare, files


def main() -> int:
    matrix = json.loads(MATRIX_PATH.read_text())
    hotspot_prefixes: list[str] = [str(item) for item in matrix.get("hotspot_prefixes", [])]
    required_always: list[str] = [str(item) for item in matrix.get("required_always", [])]
    required_on_hotspot_change: list[str] = [str(item) for item in matrix.get("required_on_hotspot_change", [])]

    git_range, changed_files = _git_changed_files()
    hotspot_touched = any(
        any(changed.startswith(prefix) for prefix in hotspot_prefixes)
        for changed in changed_files
    )

    commands: list[str] = list(required_always)
    ci_mode = os.environ.get("GITHUB_ACTIONS") == "true"
    hotspot_tests_enforced = hotspot_touched and ci_mode
    if hotspot_tests_enforced:
        commands.extend(required_on_hotspot_change)

    results: list[dict[str, Any]] = []
    overall_exit = 0
    for command in commands:
        result = _run(command)
        results.append(result)
        if result["exit_code"] != 0:
            overall_exit = 1

    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "git_range": git_range,
        "changed_files": changed_files,
        "hotspot_touched": hotspot_touched,
        "hotspot_tests_enforced": hotspot_tests_enforced,
        "commands": results,
        "overall_exit": overall_exit,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = REPORT_DIR / "latest.json"
    latest_path.write_text(json.dumps(report, indent=2))
    timestamp_path = REPORT_DIR / f"validation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    timestamp_path.write_text(json.dumps(report, indent=2))

    print(f"Validation report: {latest_path.relative_to(ROOT)}")
    print(f"Hotspot touched: {hotspot_touched}")
    if hotspot_touched and not hotspot_tests_enforced:
        print("Hotspot suites skipped outside CI; they are enforced when GITHUB_ACTIONS=true.")
    print(f"Commands executed: {len(commands)}")
    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main())
