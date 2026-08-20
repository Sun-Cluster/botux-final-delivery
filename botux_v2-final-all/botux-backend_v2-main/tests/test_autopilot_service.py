from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.autopilot.service import AutopilotService


def _policy() -> dict[str, object]:
    return {
        "enabled": True,
        "mode": "recommend",
        "evaluation_window_days": 7,
        "shadow_min_closed_trades": 4,
        "shadow_max_win_rate": 45.0,
        "shadow_max_pnl_pct": -2.0,
        "reactivate_interval_seconds": 3600,
        "reactivate_min_closed_trades": 3,
        "reactivate_min_win_rate": 60.0,
        "reactivate_min_pnl_pct": 1.0,
    }


def test_autopilot_service_shadows_underperforming_active_bot() -> None:
    now = datetime.now(timezone.utc)
    decisions = AutopilotService().evaluate(
        snapshot={
            "bot_performance_context": {
                "copycat": {
                    "bot_id": "copycat",
                    "enabled": True,
                    "lifecycle_state": "paper",
                    "autopilot_state": "active",
                    "autopilot_changed_at": None,
                    "recent_metrics": {"closed_trades": 4, "win_rate": 25.0, "pnl_pct_total": -4.5},
                    "shadow_metrics": {},
                }
            }
        },
        policy=_policy(),
        now=now,
    )
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["bot_id"] == "copycat"
    assert decision["previous_state"] == "active"
    assert decision["recommended_state"] == "shadow"
    assert "underperforming_recent_window" in decision["reason_codes"]


def test_autopilot_service_keeps_shadow_until_interval_and_recovery_pass() -> None:
    now = datetime.now(timezone.utc)
    changed_at = now - timedelta(minutes=30)
    decisions = AutopilotService().evaluate(
        snapshot={
            "bot_performance_context": {
                "nugget_bot": {
                    "bot_id": "nugget_bot",
                    "enabled": True,
                    "lifecycle_state": "paper",
                    "autopilot_state": "shadow",
                    "autopilot_changed_at": changed_at.isoformat(),
                    "recent_metrics": {"closed_trades": 5, "win_rate": 70.0, "pnl_pct_total": 4.5},
                    "shadow_metrics": {"closed_trades": 5, "win_rate": 70.0, "pnl_pct_total": 4.5},
                }
            }
        },
        policy=_policy(),
        now=now,
    )
    assert decisions[0]["recommended_state"] == "shadow"
    assert "reactivation_interval_pending" in decisions[0]["reason_codes"]


def test_autopilot_service_reactivates_shadow_bot_after_recovery() -> None:
    now = datetime.now(timezone.utc)
    changed_at = now - timedelta(hours=2)
    decisions = AutopilotService().evaluate(
        snapshot={
            "bot_performance_context": {
                "drifter": {
                    "bot_id": "drifter",
                    "enabled": True,
                    "lifecycle_state": "paper",
                    "autopilot_state": "shadow",
                    "autopilot_changed_at": changed_at.isoformat(),
                    "recent_metrics": {"closed_trades": 4, "win_rate": 75.0, "pnl_pct_total": 3.2},
                    "shadow_metrics": {"closed_trades": 4, "win_rate": 75.0, "pnl_pct_total": 3.2},
                }
            }
        },
        policy=_policy(),
        now=now,
    )
    assert decisions[0]["previous_state"] == "shadow"
    assert decisions[0]["recommended_state"] == "active"
    assert "shadow_window_recovered" in decisions[0]["reason_codes"]
