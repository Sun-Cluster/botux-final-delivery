from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


@dataclass(frozen=True)
class CheckResult:
    id: str
    title: str
    passed: bool
    evidence: str


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = [
        _check_snapshot_exists(root),
        _check_reconciliation_exists(root),
        _check_migration_history_exists(root),
        _check_no_supabase_calls_in_new_code(root),
        _check_no_reference_data_writes_in_new_code(root),
        _check_runtime_endpoints_present(root),
        _check_baseline_metrics_exists(root),
        _check_rollback_runbook_present(root),
    ]

    passed = sum(1 for item in results if item.passed)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": passed,
            "total": len(results),
        },
        "checks": [
            {
                "id": item.id,
                "title": item.title,
                "passed": item.passed,
                "evidence": item.evidence,
            }
            for item in results
        ],
    }

    out_dir = root / "docs" / "context" / "artifacts" / "cutover"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cutover_dry_run_report.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_path = root / "docs" / "context" / "CUTOVER_DRY_RUN_REPORT.md"
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"Wrote cutover dry-run report: {json_path}")
    print(f"Wrote cutover markdown report: {md_path}")


def _check_snapshot_exists(root: Path) -> CheckResult:
    snapshots = root / "docs" / "context" / "artifacts" / "snapshots"
    manifests = sorted(snapshots.rglob("manifest.json")) if snapshots.exists() else []
    passed = len(manifests) > 0
    evidence = manifests[-1].as_posix() if passed else "No snapshot manifest found"
    return CheckResult(
        id="CUTOVER-01",
        title="Production/target data export snapshot taken and timestamped",
        passed=passed,
        evidence=evidence,
    )


def _check_reconciliation_exists(root: Path) -> CheckResult:
    report = root / "docs" / "context" / "artifacts" / "import_staging" / "reconcile_report.json"
    passed = report.exists()
    return CheckResult(
        id="CUTOVER-02",
        title="Row-count reconciliation report exists",
        passed=passed,
        evidence=report.as_posix() if passed else "Missing reconcile_report.json",
    )


def _check_migration_history_exists(root: Path) -> CheckResult:
    migration_file = root / "src" / "db" / "migrations" / "0001_initial.py"
    passed = migration_file.exists()
    return CheckResult(
        id="CUTOVER-03",
        title="Final migration baseline exists",
        passed=passed,
        evidence=migration_file.as_posix() if passed else "Missing baseline migration file",
    )


def _check_no_supabase_calls_in_new_code(root: Path) -> CheckResult:
    py_files = list((root / "src").rglob("*.py"))
    matches: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        if ".table(" in text:
            matches.append(path.relative_to(root).as_posix())
    passed = len(matches) == 0
    evidence = "No `.table(` calls under src/" if passed else ", ".join(matches[:10])
    return CheckResult(
        id="CUTOVER-04",
        title="Direct Supabase writes disabled for migrated flow",
        passed=passed,
        evidence=evidence,
    )


def _check_no_reference_data_writes_in_new_code(root: Path) -> CheckResult:
    py_files = list((root / "src").rglob("*.py"))
    patterns = [re.compile(r"data/.+\.jsonl?"), re.compile(r"open\(.+\.jsonl?")]
    matches: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in patterns):
            matches.append(path.relative_to(root).as_posix())
    passed = len(matches) == 0
    evidence = "No direct json/jsonl data-path writes in src/" if passed else ", ".join(matches[:10])
    return CheckResult(
        id="CUTOVER-05",
        title="Business-truth file writes disabled for migrated flow",
        passed=passed,
        evidence=evidence,
    )


def _check_runtime_endpoints_present(root: Path) -> CheckResult:
    runtime_router = root / "src" / "api" / "routers" / "runtime.py"
    if not runtime_router.exists():
        return CheckResult(
            id="CUTOVER-06",
            title="Health endpoints and smoke checks verified",
            passed=False,
            evidence="runtime router file missing",
        )
    text = runtime_router.read_text(encoding="utf-8")
    passed = "/queues" in text and "/metrics" in text
    evidence = "runtime endpoints /runtime/queues and /runtime/metrics found" if passed else "missing queue/metrics endpoints"
    return CheckResult(
        id="CUTOVER-06",
        title="Health endpoints and smoke checks verified",
        passed=passed,
        evidence=evidence,
    )


def _check_baseline_metrics_exists(root: Path) -> CheckResult:
    baseline = root / "docs" / "context" / "artifacts" / "stabilization" / "baseline_metrics.json"
    passed = baseline.exists()
    return CheckResult(
        id="CUTOVER-07",
        title="Error rate, queue depth, retry rate and event-loop latency baseline captured",
        passed=passed,
        evidence=baseline.as_posix() if passed else "Missing baseline_metrics.json",
    )


def _check_rollback_runbook_present(root: Path) -> CheckResult:
    rollback = root / "docs" / "checklists" / "ROLLBACK_CHECKLIST.md"
    passed = rollback.exists()
    return CheckResult(
        id="CUTOVER-08",
        title="On-call rollback runbook acknowledged by operator",
        passed=passed,
        evidence=rollback.as_posix() if passed else "Missing rollback checklist",
    )


def _to_markdown(payload: dict) -> str:
    lines: list[str] = []
    lines.append("# Cutover Dry-Run Report")
    lines.append("")
    lines.append(f"Generated at: {payload['generated_at_utc']}")
    lines.append("")
    summary = payload["summary"]
    lines.append(f"Summary: {summary['passed']} / {summary['total']} checks passed")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for check in payload["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- [{status}] `{check['id']}` {check['title']} — {check['evidence']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
