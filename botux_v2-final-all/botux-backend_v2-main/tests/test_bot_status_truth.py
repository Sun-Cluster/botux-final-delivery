from __future__ import annotations

from api.routers.bot_registry import _dashboard_profile, _merge_performance, _normalize_bot_id, _runtime_status
from api.routers.compat.api_extra_router import _reward_bot_performance
from api.routers.intel_compat import _signal_reason_code


def test_runtime_status_returns_active_when_enabled_and_scheduler_active() -> None:
    assert _runtime_status(lifecycle="paper", enabled=True, scheduler_active=True) == "active"


def test_runtime_status_does_not_return_inactive_for_enabled_bot_when_scheduler_inactive() -> None:
    assert _runtime_status(lifecycle="shadow", enabled=True, scheduler_active=False) == "idle"


def test_runtime_status_returns_inactive_when_disabled() -> None:
    assert _runtime_status(lifecycle="live", enabled=False, scheduler_active=True) == "inactive"


def test_runtime_status_returns_offline_for_terminal_lifecycle() -> None:
    assert _runtime_status(lifecycle="retired", enabled=True, scheduler_active=True) == "offline"


def test_merge_performance_hydrates_dashboard_metrics_from_scorecard() -> None:
    payload = _merge_performance(
        existing={"open_trades": 2},
        scorecard={
            "total_trades": 8,
            "wins": 5,
            "losses": 3,
            "win_rate": 62.5,
            "expectancy_r": 1.25,
            "gross_profit": 14.5,
            "gross_loss": 4.0,
            "sharpe": 1.8,
            "quality_score": 77.0,
            "confidence": "medium",
            "suppressed": False,
        },
    )

    assert payload["total_trades"] == 8
    assert payload["closed_trades"] == 8
    assert payload["open_trades"] == 2
    assert payload["wins"] == 5
    assert payload["losses"] == 3
    assert payload["avg_r"] == 1.25
    assert payload["total_pnl"] == 10.5


def test_normalize_bot_id_maps_legacy_aliases() -> None:
    assert _normalize_bot_id("ausmining") == "nugget_bot"
    assert _normalize_bot_id("swingtrade") == "drifter"


def test_reward_bot_performance_builds_bot_level_avg_r_payload() -> None:
    payload = _reward_bot_performance(
        {
            "turbo": {"total_trades": 4, "wins": 3, "losses": 1, "expectancy_r": 1.5, "win_rate": 75.0},
        }
    )

    assert payload["turbo"]["trades"] == 4
    assert payload["turbo"]["wins"] == 3
    assert payload["turbo"]["losses"] == 1
    assert payload["turbo"]["avg_r"] == 1.5
    assert payload["turbo"]["total_r"] == 6.0


def test_signal_reason_code_prefers_failure_reason_for_failed_signal() -> None:
    code = _signal_reason_code(
        {
            "status": "failed",
            "blocked_reason": "bypass_council",
            "metadata": {
                "approval_reason": "bypass_council",
                "failure_reason": "alpaca_not_configured",
            },
        }
    )

    assert code == "alpaca_not_configured"


def test_signal_reason_code_does_not_report_bypass_as_failure_reason() -> None:
    code = _signal_reason_code(
        {
            "status": "failed",
            "blocked_reason": "bypass_council",
            "metadata": {
                "approval_reason": "bypass_council",
            },
        }
    )

    assert code == "execution_failed"


def test_dashboard_profile_keeps_canonical_bot_id_over_profile_payload() -> None:
    profile = _dashboard_profile(
        "copycat",
        {"bot_id": "tradecopy", "enabled": True, "lifecycle_state": "paper"},
        scheduler_active=False,
    )

    assert profile["bot_id"] == "copycat"
