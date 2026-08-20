from __future__ import annotations

import asyncio

from tortoise import Tortoise

from api.routers.autopilot import (
    AutopilotPolicyPatchRequest,
    autopilot_decisions,
    autopilot_policy,
    autopilot_run_detail,
    autopilot_runs,
    autopilot_status,
    autopilot_update_policy,
)
from db.repositories.autopilot_repo import AutopilotRepository


class _ContainerStub:
    scheduler = None


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


async def _run_autopilot_api_case() -> None:
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
    run = await repo.create_run(
        policy_id=int(policy["id"]),
        mode="observe",
        snapshot={"runtime_context": {"status": "ok"}},
        bots_count=1,
    )
    await repo.insert_decisions(
        run_id=int(run["id"]),
        policy_id=int(policy["id"]),
        rows=[
            {
                "bot_id": "copycat",
                "previous_state": "active",
                "recommended_state": "shadow",
                "reason_codes": ["underperforming_recent_window"],
                "evidence": {"note": "api_test"},
                "applied": False,
            }
        ],
    )
    await repo.complete_run(run_id=int(run["id"]), status="completed")

    policy_payload = await autopilot_policy()
    assert "policy" in policy_payload
    assert policy_payload["policy"]["name"] == "fleet_autopilot_mvp"

    updated = await autopilot_update_policy(
        AutopilotPolicyPatchRequest(mode="recommend", reactivate_interval_seconds=600)
    )
    assert updated["policy"]["mode"] == "recommend"
    assert updated["policy"]["reactivate_interval_seconds"] == 600

    runs = await autopilot_runs(limit=10)
    assert runs["count"] >= 1

    details = await autopilot_run_detail(run_id=int(run["id"]))
    assert details["decision_count"] == 1
    assert details["decisions"][0]["bot_id"] == "copycat"

    decisions = await autopilot_decisions(run_id=int(run["id"]), state="shadow")
    assert decisions["count"] == 1
    assert decisions["items"][0]["recommended_state"] == "shadow"

    status = await autopilot_status(container=_ContainerStub())
    assert "policy" in status
    assert "latest_run" in status
    assert status["latest_recommendation_counts"].get("shadow", 0) >= 1

    await _teardown_db()


def test_autopilot_api_surfaces_return_policy_runs_and_decisions() -> None:
    asyncio.run(_run_autopilot_api_case())
