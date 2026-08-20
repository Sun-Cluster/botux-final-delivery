from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tortoise import Tortoise

from app.services.registry.seeder import seed_registry
from db.repositories.bots_repo import BotsRepository


def test_registry_seed_backfills_missing_reference_profiles_and_preserves_metadata(tmp_path: Path) -> None:
    asyncio.run(_run_reference_backfill_case(tmp_path))


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


async def _run_reference_backfill_case(tmp_path: Path) -> None:
    await _setup_db()
    reference_path = tmp_path / "bot_profiles.json"
    reference_payload = {
        "profiles": {
            "swingtrade": {
                "display_name": "Axon Reference",
                "mission": "reference mission should not override canonical mission",
                "enabled": True,
                "allocation": {"pct": 0.9, "usd": 10000.0, "currency": "USD"},
                "risk": {"max_position_pct": 3.0, "max_daily_loss_pct": 5.0},
                "compat_probe": {"source": "reference"},
            },
            "auto_ext_demo_strategy": {
                "display_name": "Auto Demo",
                "strategy_type": "microstructure",
                "enabled": True,
                "lifecycle_state": "TESTING",
                "allocation": {"pct": 10.0, "usd": 10000.0, "currency": "USD"},
                "arena_params": {"min_combined_score": 0.58, "take_profit_pct": 0.04},
                "lineage": {"origin": "arena", "parent_strategy": "demo"},
            },
        }
    }
    reference_path.write_text(json.dumps(reference_payload), encoding="utf-8")

    seeded = await seed_registry(mode="repair", reference_profile_sources=[reference_path])
    assert seeded["reference_profiles_detected"] == 2
    assert seeded["reference_profiles_written"] >= 1

    profiles = await BotsRepository(connection=None).list_bot_profiles()

    assert "drifter" in profiles
    drifter = profiles["drifter"]
    assert drifter["mission"] == "Multi-day swing entries with controlled momentum filters."
    assert drifter["compat_probe"] == {"source": "reference"}
    assert drifter["allocation"]["pct"] == 0.32
    assert drifter["allocation"]["usd"] == 10000.0
    assert drifter["allocation"]["currency"] == "USD"
    assert drifter["execution_policy"]["capital_basis"] == "buying_power"
    assert drifter["execution_policy"]["min_buying_power_usd"] == 100.0
    assert drifter["execution_policy"]["skip_scan_when_insufficient_buying_power"] is True

    assert "auto_ext_demo_strategy" in profiles
    auto = profiles["auto_ext_demo_strategy"]
    assert auto["lifecycle_state"] == "TESTING"
    assert auto["arena_params"] == {"min_combined_score": 0.58, "take_profit_pct": 0.04}
    assert auto["lineage"] == {"origin": "arena", "parent_strategy": "demo"}

    assert "copycat" in profiles
    copycat = profiles["copycat"]
    assert copycat["enabled"] is True
    assert copycat["lifecycle_state"] == "paper"
    assert copycat["status"] == "ready"
    assert copycat["autopilot_state"] == "active"

    for bot_id in ("gambler", "nugget_bot", "evo_catalyst"):
        assert bot_id in profiles
        profile = profiles[bot_id]
        assert profile["enabled"] is True
        assert profile["lifecycle_state"] == "paper"
        assert profile["status"] == "ready"
        assert profile["autopilot_state"] == "active"

    await _teardown_db()
