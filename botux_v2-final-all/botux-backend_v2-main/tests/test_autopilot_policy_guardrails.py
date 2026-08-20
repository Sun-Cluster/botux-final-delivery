from __future__ import annotations

from app.services.autopilot.policy import normalize_policy, validate_policy


def test_normalize_policy_fills_new_autopilot_defaults() -> None:
    policy = normalize_policy({"enabled": True, "mode": "recommend"})
    assert policy["evaluation_window_days"] == 7
    assert policy["shadow_min_closed_trades"] == 4
    assert policy["reactivate_interval_seconds"] == 86400


def test_validate_policy_accepts_clean_thresholds() -> None:
    result = validate_policy(
        {
            "enabled": True,
            "mode": "constrained_apply",
            "evaluation_window_days": 7,
            "shadow_min_closed_trades": 4,
            "reactivate_min_closed_trades": 3,
        }
    )
    assert result.valid is True
    assert result.reason_codes == []
