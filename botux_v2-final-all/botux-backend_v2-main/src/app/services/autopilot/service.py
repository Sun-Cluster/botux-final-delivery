from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.repositories._common import JSONValue


class AutopilotService:
    def evaluate(
        self,
        *,
        snapshot: dict[str, JSONValue],
        policy: dict[str, JSONValue],
        now: datetime | None = None,
    ) -> list[dict[str, object]]:
        checked_at = now or datetime.now(timezone.utc)
        bot_contexts = _dict(snapshot.get("bot_performance_context"))
        decisions: list[dict[str, object]] = []
        for bot_id, raw in sorted(bot_contexts.items()):
            if not isinstance(raw, dict):
                continue
            context = {str(key): value for key, value in raw.items()}
            current_state = _state(context.get("autopilot_state"))
            changed_at = _as_datetime(context.get("autopilot_changed_at"))
            enabled = bool(context.get("enabled", False))
            lifecycle_state = str(context.get("lifecycle_state", "unknown")).strip().lower()
            recent_metrics = _dict(context.get("recent_metrics"))
            shadow_metrics = _dict(context.get("shadow_metrics"))

            recommended_state = current_state
            reason_codes: list[str] = []
            evidence: dict[str, object] = {
                "enabled": enabled,
                "lifecycle_state": lifecycle_state,
                "autopilot_state": current_state,
                "autopilot_changed_at": None if changed_at is None else changed_at.isoformat(),
                "recent_metrics": recent_metrics,
                "shadow_metrics": shadow_metrics,
                "evaluation_window_days": _as_int(policy.get("evaluation_window_days")),
            }

            if current_state == "shadow":
                recommended_state, shadow_reasons = _evaluate_shadow_recovery(
                    checked_at=checked_at,
                    changed_at=changed_at,
                    metrics=shadow_metrics,
                    policy=policy,
                )
                reason_codes.extend(shadow_reasons)
            else:
                recommended_state, active_reasons = _evaluate_active_performance(
                    metrics=recent_metrics,
                    policy=policy,
                )
                reason_codes.extend(active_reasons)

            decisions.append(
                {
                    "bot_id": bot_id,
                    "previous_state": current_state,
                    "recommended_state": recommended_state,
                    "reason_codes": sorted(set(reason_codes)),
                    "applied": False,
                    "evidence": evidence,
                }
            )
        return decisions


def _evaluate_active_performance(
    *,
    metrics: dict[str, object],
    policy: dict[str, JSONValue],
) -> tuple[str, list[str]]:
    closed_trades = _as_int(metrics.get("closed_trades"))
    win_rate = _as_float(metrics.get("win_rate"))
    pnl_pct = _as_float(metrics.get("pnl_pct_total"))
    min_closed_trades = _as_int(policy.get("shadow_min_closed_trades"))
    max_win_rate = _as_float(policy.get("shadow_max_win_rate"))
    max_pnl_pct = _as_float(policy.get("shadow_max_pnl_pct"))

    if closed_trades < min_closed_trades:
        return "active", ["insufficient_recent_trades"]
    if win_rate is None or pnl_pct is None:
        return "active", ["missing_recent_performance"]
    if win_rate <= max_win_rate and pnl_pct <= max_pnl_pct:
        return "shadow", ["underperforming_recent_window"]
    return "active", ["recent_window_ok"]


def _evaluate_shadow_recovery(
    *,
    checked_at: datetime,
    changed_at: datetime | None,
    metrics: dict[str, object],
    policy: dict[str, JSONValue],
) -> tuple[str, list[str]]:
    interval_seconds = _as_int(policy.get("reactivate_interval_seconds"))
    min_closed_trades = _as_int(policy.get("reactivate_min_closed_trades"))
    min_win_rate = _as_float(policy.get("reactivate_min_win_rate"))
    min_pnl_pct = _as_float(policy.get("reactivate_min_pnl_pct"))
    if changed_at is None:
        return "shadow", ["missing_shadow_timestamp"]
    elapsed = max((checked_at - changed_at).total_seconds(), 0.0)
    if elapsed < interval_seconds:
        return "shadow", ["reactivation_interval_pending"]

    closed_trades = _as_int(metrics.get("closed_trades"))
    win_rate = _as_float(metrics.get("win_rate"))
    pnl_pct = _as_float(metrics.get("pnl_pct_total"))
    if closed_trades < min_closed_trades:
        return "shadow", ["insufficient_shadow_window_trades"]
    if win_rate is None or pnl_pct is None:
        return "shadow", ["missing_shadow_window_performance"]
    if win_rate >= min_win_rate and pnl_pct >= min_pnl_pct:
        return "active", ["shadow_window_recovered"]
    return "shadow", ["shadow_window_not_recovered"]


def summarize_outcomes(
    *,
    rows: list[object],
    fallback_since: datetime | None = None,
) -> dict[str, object]:
    wins = 0
    losses = 0
    breakeven = 0
    pnl_pct_total = 0.0
    closed_at_values: list[datetime] = []
    for row in rows:
        outcome = str(getattr(row, "outcome", "")).strip().lower()
        pnl_pct = _as_float(getattr(row, "pnl_pct", None)) or 0.0
        closed_at = _as_datetime(getattr(row, "closed_at", None))
        if closed_at is not None:
            closed_at_values.append(closed_at)
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        elif outcome == "breakeven":
            breakeven += 1
        pnl_pct_total += pnl_pct
    closed_trades = wins + losses + breakeven
    decisive = wins + losses
    win_rate = (wins / decisive * 100.0) if decisive > 0 else None
    return {
        "closed_trades": closed_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "pnl_pct_total": round(pnl_pct_total, 4),
        "window_started_at": None if fallback_since is None else fallback_since.isoformat(),
        "window_ended_at": None if not closed_at_values else max(closed_at_values).isoformat(),
    }


def recent_window_start(*, now: datetime, days: int) -> datetime:
    return now - timedelta(days=max(days, 1))


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _state(value: object) -> str:
    normalized = str(value or "active").strip().lower()
    if normalized == "shadow":
        return "shadow"
    return "active"


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
