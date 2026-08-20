from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from tortoise import Tortoise

from app.services.autopilot.policy import AutopilotPolicyService
from db.models import SignalRecord, TradeOutcomeRecord
from db.repositories.autopilot_repo import AutopilotRepository
from db.repositories.bots_repo import BotsRepository
from infra.scheduler.jobs import register_jobs
from runtime.container import build_container


@contextmanager
def _temp_env(updates: dict[str, str]):
    previous = {key: os.environ.get(key) for key in updates}
    for key, value in updates.items():
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _setup_db() -> None:
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {"models": {"models": ["src.db.models"], "default_connection": "default"}},
            "use_tz": True,
            "timezone": "UTC",
        },
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas(safe=True)


async def _teardown_db() -> None:
    await Tortoise.close_connections()


async def _seed_bots() -> None:
    bots = BotsRepository(connection=None)
    await bots.upsert_bot_profile("copycat", {"enabled": True, "lifecycle_state": "paper", "autopilot_state": "active"})
    await bots.upsert_bot_profile("nugget_bot", {"enabled": True, "lifecycle_state": "paper", "autopilot_state": "active"})
    await bots.upsert_bot_profile("drifter", {"enabled": True, "lifecycle_state": "paper", "autopilot_state": "active"})
    await bots.upsert_bot_profile("gambler", {"enabled": True, "lifecycle_state": "paper", "autopilot_state": "active"})


async def _seed_underperforming_copycat() -> None:
    now = datetime.now(timezone.utc)
    signal = await SignalRecord.create(
        signal_id="copycat-shadow-seed",
        symbol="AAPL",
        action="buy",
        status="executed",
        source="tradecopy",
        metadata={},
    )
    for idx, pnl_pct in enumerate([-1.0, -1.2, -0.8, -1.5], start=1):
        await TradeOutcomeRecord.create(
            signal=signal,
            order=None,
            symbol="AAPL",
            outcome="loss",
            pnl_pct=pnl_pct,
            trade_id=f"copycat-loss-{idx}",
            action="buy",
            quantity=1.0,
            entry_price=100.0,
            exit_price=100.0 + pnl_pct,
            close_reason="stop_loss",
            bot_id="copycat",
            source="tradecopy",
            broker_order_id=None,
            broker_name="paper",
            market="us_equities",
            order_type="market",
            features={},
            created_at=now - timedelta(days=1),
            closed_at=now - timedelta(hours=1),
        )


async def _run_scheduler_autopilot_case() -> None:
    await _setup_db()
    await _seed_bots()
    with _temp_env(
        {
            "BOTUX_DB_URI": "sqlite://:memory:",
            "BOTUX_SKIP_DB_INIT": "1",
            "BOTUX_AUTOPILOT_ENABLED": "1",
            "BOTUX_AUTOPILOT_INTERVAL_SECONDS": "300",
            "BOTUX_NEWS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS": "0",
            "BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS": "0",
            "BOTUX_POSITION_MONITOR_INTERVAL_SECONDS": "0",
            "BOTUX_RECONCILE_INTERVAL_SECONDS": "0",
            "BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS": "0",
            "BOTUX_SCOUT_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_TRADECOPY_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_OPTIONS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SWINGTRADE_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_MINER_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_EVO_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_RUNTIME_PROOF_INTERVAL_SECONDS": "0",
        }
    ):
        container = build_container()
        jobs = await register_jobs(container)
        autopilot_job = next(job for job in jobs if job.name == "autopilot.evaluate")
        await autopilot_job.run()

    repo = AutopilotRepository(connection=None)
    latest_run = await repo.latest_run()
    assert latest_run is not None
    assert latest_run["status"] == "completed"
    decisions = await repo.list_decisions(run_id=int(latest_run["id"]), limit=20)
    assert len(decisions) == 4
    assert {row["bot_id"] for row in decisions} == {"nugget_bot", "drifter", "gambler", "copycat"}
    await _teardown_db()


def test_scheduler_autopilot_job_runs_cycle_and_persists_output() -> None:
    asyncio.run(_run_scheduler_autopilot_case())


async def _run_autopilot_mode_case(mode: str) -> None:
    await _setup_db()
    await _seed_bots()
    with _temp_env(
        {
            "BOTUX_DB_URI": "sqlite://:memory:",
            "BOTUX_SKIP_DB_INIT": "1",
            "BOTUX_AUTOPILOT_ENABLED": "1",
            "BOTUX_AUTOPILOT_INTERVAL_SECONDS": "300",
            "BOTUX_NEWS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS": "0",
            "BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS": "0",
            "BOTUX_POSITION_MONITOR_INTERVAL_SECONDS": "0",
            "BOTUX_RECONCILE_INTERVAL_SECONDS": "0",
            "BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS": "0",
            "BOTUX_SCOUT_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_TRADECOPY_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_OPTIONS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SWINGTRADE_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_MINER_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_EVO_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_RUNTIME_PROOF_INTERVAL_SECONDS": "0",
        }
    ):
        await AutopilotPolicyService().update_policy({"mode": mode, "enabled": True})
        container = build_container()
        jobs = await register_jobs(container)
        autopilot_job = next(job for job in jobs if job.name == "autopilot.evaluate")
        await autopilot_job.run()

    repo = AutopilotRepository(connection=None)
    latest_run = await repo.latest_run()
    assert latest_run is not None
    assert latest_run["mode"] == mode
    decisions = await repo.list_decisions(run_id=int(latest_run["id"]), limit=20)
    assert all(row["applied"] is False for row in decisions)
    await _teardown_db()


def test_autopilot_runtime_modes_observe_and_recommend_persist_without_apply() -> None:
    asyncio.run(_run_autopilot_mode_case("observe"))
    asyncio.run(_run_autopilot_mode_case("recommend"))


async def _run_constrained_apply_case() -> None:
    await _setup_db()
    await _seed_bots()
    await _seed_underperforming_copycat()
    with _temp_env(
        {
            "BOTUX_DB_URI": "sqlite://:memory:",
            "BOTUX_SKIP_DB_INIT": "1",
            "BOTUX_AUTOPILOT_ENABLED": "1",
            "BOTUX_AUTOPILOT_INTERVAL_SECONDS": "300",
            "BOTUX_NEWS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS": "0",
            "BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS": "0",
            "BOTUX_POSITION_MONITOR_INTERVAL_SECONDS": "0",
            "BOTUX_RECONCILE_INTERVAL_SECONDS": "0",
            "BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS": "0",
            "BOTUX_SCOUT_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_TRADECOPY_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_OPTIONS_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_SWINGTRADE_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_MINER_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_EVO_SCAN_INTERVAL_SECONDS": "0",
            "BOTUX_RUNTIME_PROOF_INTERVAL_SECONDS": "0",
        }
    ):
        await AutopilotPolicyService().update_policy({"mode": "constrained_apply", "enabled": True})
        container = build_container()
        jobs = await register_jobs(container)
        autopilot_job = next(job for job in jobs if job.name == "autopilot.evaluate")
        await autopilot_job.run()

    repo = AutopilotRepository(connection=None)
    latest_run = await repo.latest_run()
    assert latest_run is not None
    assert latest_run["mode"] == "constrained_apply"
    decisions = await repo.list_decisions(run_id=int(latest_run["id"]), limit=20)
    assert any(bool(row["applied"]) for row in decisions if row["bot_id"] == "copycat")
    copycat = await BotsRepository(connection=None).get_bot_profile("copycat")
    assert copycat is not None
    assert bool(copycat.get("enabled")) is True
    assert copycat.get("lifecycle_state") == "paper"
    assert copycat.get("autopilot_state") == "shadow"
    await _teardown_db()


def test_autopilot_constrained_apply_sets_shadow_without_disabling_bot() -> None:
    asyncio.run(_run_constrained_apply_case())
