from __future__ import annotations

import asyncio

from tortoise import Tortoise

from db.repositories.autopilot_repo import AutopilotRepository


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


async def _run_autopilot_repo_case() -> None:
    await _setup_db()
    repo = AutopilotRepository(connection=None)

    policy = await repo.upsert_policy(
        name="fleet_autopilot_mvp",
        enabled=True,
        mode="observe",
        evaluation_window_days=7,
        shadow_min_closed_trades=4,
        shadow_max_win_rate=45.0,
        shadow_max_pnl_pct=-2.0,
        reactivate_interval_seconds=86400,
        reactivate_min_closed_trades=3,
        reactivate_min_win_rate=60.0,
        reactivate_min_pnl_pct=1.0,
    )
    assert policy["name"] == "fleet_autopilot_mvp"
    assert policy["mode"] == "observe"
    assert policy["enabled"] is True

    active_policy = await repo.get_active_policy()
    assert active_policy is not None
    assert active_policy["id"] == policy["id"]

    run = await repo.create_run(
        policy_id=int(policy["id"]),
        mode="observe",
        snapshot={"runtime_context": {"status": "ok"}},
        bots_count=2,
    )
    assert run["status"] == "running"
    assert run["bots_count"] == 2

    inserted = await repo.insert_decisions(
        run_id=int(run["id"]),
        policy_id=int(policy["id"]),
        rows=[
            {
                "bot_id": "copycat",
                "previous_state": "active",
                "recommended_state": "shadow",
                "reason_codes": ["underperforming_recent_window"],
                "evidence": {"win_rate": 25.0, "pnl_pct_total": -4.5},
                "applied": False,
            },
            {
                "bot_id": "drifter",
                "previous_state": "shadow",
                "recommended_state": "active",
                "reason_codes": ["shadow_window_recovered"],
                "evidence": {"win_rate": 75.0, "pnl_pct_total": 3.2},
                "applied": True,
            },
        ],
    )
    assert len(inserted) == 2
    assert inserted[0]["bot_id"] == "copycat"
    assert inserted[1]["recommended_state"] == "active"

    completed = await repo.complete_run(run_id=int(run["id"]), status="completed")
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None

    runs = await repo.list_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["id"] == run["id"]

    filtered = await repo.list_decisions(run_id=int(run["id"]), bot_id="drifter")
    assert len(filtered) == 1
    assert filtered[0]["reason_codes"] == ["shadow_window_recovered"]

    activated = await repo.list_decisions(recommended_state="active")
    assert len(activated) == 1
    assert activated[0]["bot_id"] == "drifter"

    latest_index = await repo.latest_decision_index()
    assert set(latest_index.keys()) == {"copycat", "drifter"}
    assert latest_index["drifter"]["recommended_state"] == "active"

    await _teardown_db()


def test_autopilot_repository_persists_policy_runs_and_decisions() -> None:
    asyncio.run(_run_autopilot_repo_case())
