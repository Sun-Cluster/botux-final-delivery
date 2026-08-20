from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tortoise import Tortoise

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import load_configs
from db.tortoise_config import build_tortoise_config

EXPECTED_TABLES = {
    "audit_logs",
    "autopilot_decisions",
    "autopilot_policies",
    "autopilot_runs",
    "bot_profiles",
    "council_decisions",
    "executions",
    "gate_failures",
    "orders",
    "outbox_events",
    "positions_snapshots",
    "signals",
    "signal_events",
    "trade_outcomes",
    "strategy_registry",
    "system_configs",
}


async def _verify_tables() -> dict[str, object]:
    settings = load_configs()
    await Tortoise.init(config=build_tortoise_config(settings))
    conn = Tortoise.get_connection("default")
    sql = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema='public'
    """
    _, rows = await conn.execute_query(sql)
    actual_tables = {str(row[0]) for row in rows}
    await Tortoise.close_connections()
    missing = sorted(EXPECTED_TABLES - actual_tables)
    present = sorted(EXPECTED_TABLES & actual_tables)
    return {
        "expected_count": len(EXPECTED_TABLES),
        "present_count": len(present),
        "missing": missing,
        "present": present,
        "all_expected_present": len(missing) == 0,
    }


async def _verify_bot_profiles_columns() -> dict[str, object]:
    settings = load_configs()
    await Tortoise.init(config=build_tortoise_config(settings))
    conn = Tortoise.get_connection("default")
    sql = """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='bot_profiles'
    """
    _, rows = await conn.execute_query(sql)
    await Tortoise.close_connections()
    actual_columns = {str(row[0]) for row in rows}
    required_columns = {
        "bot_id",
        "display_name",
        "mission",
        "strategy_type",
        "horizon",
        "market",
        "broker",
        "mode",
        "lifecycle_state",
        "status",
        "icon",
        "intel_source",
        "notes",
        "enabled",
        "metadata",
        "updated_at",
    }
    removed_columns = {
        "profile_eligible",
        "performance_owner",
        "allocation_pct",
        "allocation_max_positions",
        "risk_per_trade_pct",
        "risk_daily_loss_pct",
        "risk_max_notional",
        "order_types_csv",
        "allowed_brokers_csv",
        "compat_aliases_csv",
    }
    missing_required = sorted(required_columns - actual_columns)
    still_present_removed = sorted(actual_columns & removed_columns)
    return {
        "required_columns": sorted(required_columns),
        "removed_columns": sorted(removed_columns),
        "missing_required": missing_required,
        "still_present_removed": still_present_removed,
        "ok": (len(missing_required) == 0 and len(still_present_removed) == 0),
    }


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    combined = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, combined


def _ensure_docker_available() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker command not found")


def _wait_postgres_healthy(cwd: Path, *, timeout_seconds: int = 90) -> tuple[bool, str]:
    ps_cmd = ["docker", "compose", "ps", "-q", "postgres"]
    code, output = _run(ps_cmd, cwd)
    if code != 0:
        return False, output
    container_id = output.strip()
    if not container_id:
        return False, "postgres container id not found"

    end_time = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < end_time:
        inspect_cmd = [
            "docker",
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container_id,
        ]
        code, status_output = _run(inspect_cmd, cwd)
        if code != 0:
            return False, status_output
        status = status_output.strip().lower()
        last_status = status
        if status == "healthy":
            return True, status
        if status in {"unhealthy", "exited", "dead"}:
            return False, status
        time.sleep(2)
    return False, f"timeout_waiting_health(last={last_status})"


async def _main() -> None:
    root = ROOT_DIR
    _ensure_docker_available()

    steps: list[dict[str, object]] = []
    compose_up_cmd = ["docker", "compose", "up", "-d", "postgres"]
    code, output = _run(compose_up_cmd, root)
    steps.append({"step": "docker_compose_up", "ok": code == 0, "output": output[-2000:]})
    if code != 0:
        _write_report(root, steps, {"ok": False, "reason": "docker compose up failed"})
        raise SystemExit(1)

    healthy, health_output = _wait_postgres_healthy(root)
    steps.append({"step": "postgres_healthcheck", "ok": healthy, "output": health_output})
    if not healthy:
        _write_report(root, steps, {"ok": False, "reason": "postgres not healthy"})
        raise SystemExit(1)

    migrate_cmd = ["make", "db-migrate-up"]
    code, output = _run(migrate_cmd, root)
    steps.append({"step": "db_migrate_up", "ok": code == 0, "output": output[-2000:]})
    if code != 0:
        _write_report(root, steps, {"ok": False, "reason": "db migrate failed"})
        raise SystemExit(1)

    table_report = await _verify_tables()
    steps.append({"step": "verify_expected_tables", "ok": bool(table_report["all_expected_present"]), "report": table_report})
    bot_profile_column_report = await _verify_bot_profiles_columns()
    steps.append(
        {
            "step": "verify_bot_profiles_columns",
            "ok": bool(bot_profile_column_report["ok"]),
            "report": bot_profile_column_report,
        }
    )
    ok = bool(table_report["all_expected_present"]) and bool(bot_profile_column_report["ok"])
    summary: dict[str, object] = {
        "ok": ok,
        "table_report": table_report,
        "bot_profile_column_report": bot_profile_column_report,
    }
    _write_report(root, steps, summary)
    if not ok:
        raise SystemExit(1)


def _write_report(root: Path, steps: list[dict[str, object]], summary: dict[str, object]) -> None:
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "summary": summary,
    }
    out_dir = root / "docs" / "context" / "artifacts" / "foundation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "foundation_verification_report.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    out_md = root / "docs" / "context" / "FOUNDATION_VERIFICATION_REPORT.md"
    lines = [
        "# Foundation Verification Report",
        "",
        f"Generated at: {report['generated_at_utc']}",
        "",
        "## Steps",
        "",
    ]
    for item in steps:
        status = "PASS" if item.get("ok") else "FAIL"
        lines.append(f"- [{status}] `{item.get('step')}`")
    lines.extend(["", "## Summary", "", f"- OK: `{summary.get('ok')}`"])
    table_report = summary.get("table_report")
    if isinstance(table_report, dict):
        lines.append(f"- Expected tables: `{table_report.get('expected_count')}`")
        lines.append(f"- Present tables: `{table_report.get('present_count')}`")
        missing = table_report.get("missing", [])
        lines.append(f"- Missing: `{missing}`")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
