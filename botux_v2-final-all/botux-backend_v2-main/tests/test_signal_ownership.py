from __future__ import annotations

from app.services.signals.ownership import build_signal_ownership, infer_execution_bot_id


def test_build_signal_ownership_maps_evo_source_to_evo_catalyst() -> None:
    ownership = build_signal_ownership(
        source="evo_catalyst",
        symbol="WDS.AX",
        lane_hint="evo_catalyst",
        strategy_hint="evo_catalyst_event",
        metadata=None,
    )
    assert ownership["execution_bot_id"] == "evo_catalyst"
    assert ownership["bot_id"] == "evo_catalyst"


def test_infer_execution_bot_id_maps_evo_lane_hint() -> None:
    payload = {
        "source": "unknown",
        "symbol": "IGO.AX",
        "lane_hint": "evo_catalyst",
        "strategy_hint": "evo_catalyst_event",
        "metadata": {},
    }
    assert infer_execution_bot_id(payload) == "evo_catalyst"
