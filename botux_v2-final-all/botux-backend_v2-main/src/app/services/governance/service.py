from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import cast

from app.services.measurement.service import calculate_scorecard
from db.models import SignalRecord, TradeOutcomeRecord
from db.repositories._common import JSONValue
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork

_PROMOTION_TRADE_THRESHOLD = 30
_PROMOTION_WIN_RATE_MIN = 45.0
_PROMOTION_EXPECTANCY_MIN = 0.0
_PROMOTION_DRAWDOWN_MAX = 20.0
_SHADOW_SIGNAL_LIMIT = 600
_OUTCOME_LIMIT = 1200


class GovernanceService:
    async def strategy_shadow_report(self, *, persist: bool = False, limit: int = _SHADOW_SIGNAL_LIMIT) -> dict[str, object]:
        strategies = await self._strategies()
        signals = await self._recent_signals(limit=limit)
        rows: list[dict[str, object]] = []
        for strategy_id, metadata in strategies.items():
            lifecycle_state = str(metadata.get("lifecycle_state", "unknown")).lower()
            if lifecycle_state not in {"paper", "shadow"}:
                continue
            row = self._shadow_row(strategy_id, metadata, signals)
            rows.append(row)
        rows.sort(key=lambda item: str(item["strategy_id"]))
        if persist:
            await self._persist_rows("strategy_shadow_metrics", rows)
        return {
            "strategies": rows,
            "count": len(rows),
            "signals_in_window": len(signals),
            "generated_at": _iso_now(),
        }

    async def promotion_readiness_report(self, *, persist: bool = False) -> dict[str, object]:
        strategies = await self._strategies()
        shadow_report = await self.strategy_shadow_report(persist=False)
        shadow_items = cast(list[dict[str, object]], shadow_report["strategies"])
        shadow_by_strategy = {
            str(item["strategy_id"]): item
            for item in shadow_items
            if isinstance(item, dict) and "strategy_id" in item
        }
        scorecards = await self._scorecards_by_bot(limit=_OUTCOME_LIMIT, window=100)
        decay_by_bot = await self._edge_decay_by_bot(limit=_OUTCOME_LIMIT)
        rows: list[dict[str, object]] = []
        for strategy_id, metadata in strategies.items():
            lifecycle_state = str(metadata.get("lifecycle_state", "unknown")).lower()
            if lifecycle_state not in {"paper", "shadow", "live", "scaled", "demoted"}:
                continue
            row = self._promotion_row(
                strategy_id,
                metadata,
                shadow_metrics=shadow_by_strategy.get(strategy_id),
                scorecards=scorecards,
                decay_by_bot=decay_by_bot,
            )
            rows.append(row)
        rows.sort(key=lambda item: str(item["strategy_id"]))
        if persist:
            await self._persist_rows("promotion_readiness", rows)
        return {
            "items": rows,
            "count": len(rows),
            "generated_at": _iso_now(),
        }

    async def bot_lifecycle_evidence(self, bot_id: str) -> dict[str, object]:
        scorecards = await self._scorecards_by_bot(limit=_OUTCOME_LIMIT, window=100)
        decay_by_bot = await self._edge_decay_by_bot(limit=_OUTCOME_LIMIT)
        card = scorecards.get(bot_id)
        decay = decay_by_bot.get(bot_id)
        if card is None:
            return {
                "bot_id": bot_id,
                "trade_count": 0,
                "quality_score": 0.0,
                "edge_status": "INSUFFICIENT_DATA",
                "decay_severity": "INSUFFICIENT_DATA",
                "generated_at": _iso_now(),
            }
        return {
            "bot_id": bot_id,
            "trade_count": _as_int(card.get("total_trades"), 0),
            "quality_score": _as_float(card.get("quality_score"), 0.0),
            "quality_tier": _quality_tier(_as_float(card.get("quality_score"), 0.0)),
            "edge_status": str(card.get("edge_status", "INSUFFICIENT_DATA")),
            "confidence": str(card.get("confidence", "very_low")),
            "decay_severity": str((decay or {}).get("severity", "INSUFFICIENT_DATA")),
            "decay_signals": (decay or {}).get("signals", []),
            "generated_at": _iso_now(),
        }

    async def strategy_lifecycle_evidence(self, strategy_id: str) -> dict[str, object]:
        readiness = await self.promotion_readiness_report(persist=False)
        for item in cast(list[dict[str, object]], readiness["items"]):
            if isinstance(item, dict) and str(item.get("strategy_id")) == strategy_id:
                return item
        return {
            "strategy_id": strategy_id,
            "candidacy": "hold",
            "ready": False,
            "warnings": ["no_governance_evidence"],
            "generated_at": _iso_now(),
        }

    async def _strategies(self) -> dict[str, dict]:
        try:
            async with UnitOfWork() as uow:
                return await BotsRepository(connection=uow.connection).list_strategy_registry()
        except Exception:
            return {}

    async def _recent_signals(self, *, limit: int) -> list[dict[str, object]]:
        try:
            rows = await SignalRecord.all().order_by("-created_at").limit(limit)
        except Exception:
            return []
        result: list[dict[str, object]] = []
        for row in rows:
            metadata = row.metadata if isinstance(row.metadata, dict) else {}
            result.append(
                {
                    "signal_id": row.signal_id,
                    "source": row.source or "unknown",
                    "status": row.status or "unknown",
                    "score": float(row.score or 0.0),
                    "confidence": float(row.confidence or 0.0) if row.confidence is not None else None,
                    "lane_hint": row.lane_hint,
                    "strategy_hint": row.strategy_hint,
                    "bot_id": metadata.get("bot_id"),
                    "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                }
            )
        return result

    async def _scorecards_by_bot(self, *, limit: int, window: int) -> dict[str, dict[str, object]]:
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomesRepository(connection=uow.connection).list_closed_rows(limit=limit)
        except Exception:
            return {}
        by_bot: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            by_bot[_bot_id(row)].append(_trade_payload(row))
        return cast(
            dict[str, dict[str, object]],
            {bot_id: calculate_scorecard(list(reversed(trades)), window=window) for bot_id, trades in by_bot.items()},
        )

    async def _edge_decay_by_bot(self, *, limit: int) -> dict[str, dict[str, object]]:
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomesRepository(connection=uow.connection).list_closed_rows(limit=limit)
        except Exception:
            return {}
        by_bot: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in reversed(rows):
            by_bot[_bot_id(row)].append(_trade_payload(row))
        return {bot_id: _detect_edge_decay(trades) for bot_id, trades in by_bot.items()}

    async def _persist_rows(self, event_type: str, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        try:
            async with UnitOfWork() as uow:
                repo = AuditLogsRepository(connection=uow.connection)
                for row in rows:
                    await repo.append(
                        event_type=event_type,
                        payload=cast(dict[str, JSONValue], row),
                        actor="governance_service",
                    )
        except Exception:
            return

    def _shadow_row(self, strategy_id: str, metadata: dict[str, object], signals: list[dict[str, object]]) -> dict[str, object]:
        signal_sources = {str(item).lower() for item in _as_list(metadata.get("signal_sources")) if isinstance(item, str)}
        bot_ids = {str(item).lower() for item in _as_list(metadata.get("bot_ids")) if isinstance(item, str)}
        matched_signals: list[dict[str, object]] = []
        fired_count = 0
        actual_executed = 0
        honest_fallback = 0
        for signal in signals:
            matched, fired, fallback = _match_signal(signal, signal_sources=signal_sources, bot_ids=bot_ids, metadata=metadata)
            if not matched:
                continue
            matched_signals.append(signal)
            if fired:
                fired_count += 1
            if fallback:
                honest_fallback += 1
            if str(signal.get("status", "")).lower() == "executed":
                actual_executed += 1
        matched_total = len(matched_signals)
        coverage = (matched_total / len(signals)) if signals else 0.0
        execution_alignment = (actual_executed / matched_total) if matched_total else 0.0
        fallback_pct = (honest_fallback / matched_total) if matched_total else 0.0
        status_distribution = Counter(str(item.get("status", "unknown")).lower() for item in matched_signals)
        criteria_used: list[str] = []
        if signal_sources:
            criteria_used.append("signal_sources")
        if bot_ids:
            criteria_used.append("bot_ids")
        if metadata.get("min_confidence") is not None:
            criteria_used.append("min_confidence")
        if metadata.get("min_score_threshold") is not None:
            criteria_used.append("min_score_threshold")
        return {
            "strategy_id": strategy_id,
            "name": str(metadata.get("name", strategy_id)),
            "lifecycle_state": str(metadata.get("lifecycle_state", "unknown")).lower(),
            "bot_ids": sorted(bot_ids),
            "window_signals": len(signals),
            "matched_signals": matched_total,
            "would_have_fired": fired_count,
            "actual_executed": actual_executed,
            "coverage": round(coverage, 4),
            "execution_alignment": round(execution_alignment, 4),
            "honest_fallback_pct": round(fallback_pct, 4),
            "criteria_used": criteria_used,
            "status_distribution": dict(status_distribution),
            "min_confidence": _as_optional_float(metadata.get("min_confidence")),
            "min_score_threshold": _as_optional_float(metadata.get("min_score_threshold")),
            "generated_at": _iso_now(),
        }

    def _promotion_row(
        self,
        strategy_id: str,
        metadata: dict[str, object],
        *,
        shadow_metrics: dict[str, object] | None,
        scorecards: dict[str, dict[str, object]],
        decay_by_bot: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        bot_ids = [str(item) for item in _as_list(metadata.get("bot_ids")) if isinstance(item, str)]
        linked_cards = [scorecards[bot_id] for bot_id in bot_ids if bot_id in scorecards]
        linked_decays = [decay_by_bot[bot_id] for bot_id in bot_ids if bot_id in decay_by_bot]
        trade_count = sum(_as_int(card.get("total_trades"), 0) for card in linked_cards)
        quality_score = _mean([_as_float(card.get("quality_score"), 0.0) for card in linked_cards])
        win_rate = _mean([_as_float(card.get("win_rate"), 0.0) for card in linked_cards])
        expectancy_r = _mean([_as_float(card.get("expectancy_r"), 0.0) for card in linked_cards])
        max_drawdown = max((_as_float(card.get("max_drawdown_pct"), 0.0) for card in linked_cards), default=0.0)
        confidence = _worst_confidence([str(card.get("confidence", "very_low")) for card in linked_cards])
        decay_severity = _highest_decay_severity([str(item.get("severity", "INSUFFICIENT_DATA")) for item in linked_decays])
        decay_signals = [
            signal
            for item in linked_decays
            for signal in _as_list(item.get("signals"))
            if isinstance(signal, dict)
        ][:5]
        shadow_coverage = _as_optional_float((shadow_metrics or {}).get("coverage")) or 0.0
        shadow_alignment = _as_optional_float((shadow_metrics or {}).get("execution_alignment")) or 0.0
        lifecycle_state = str(metadata.get("lifecycle_state", "unknown")).lower()
        warnings: list[str] = []
        if trade_count < _PROMOTION_TRADE_THRESHOLD:
            warnings.append("insufficient_trade_count")
        if expectancy_r <= _PROMOTION_EXPECTANCY_MIN:
            warnings.append("non_positive_expectancy")
        if win_rate <= _PROMOTION_WIN_RATE_MIN:
            warnings.append("low_win_rate")
        if max_drawdown >= _PROMOTION_DRAWDOWN_MAX:
            warnings.append("drawdown_breach")
        if shadow_coverage < 0.05:
            warnings.append("low_shadow_coverage")
        if shadow_alignment < 0.4 and shadow_coverage > 0.0:
            warnings.append("weak_execution_alignment")
        if decay_severity in {"ALERT", "CRITICAL"}:
            warnings.append("edge_decay_active")
        if quality_score < 20.0:
            warnings.append("quality_score_critical")
        ready = (
            lifecycle_state in {"paper", "shadow"}
            and trade_count >= _PROMOTION_TRADE_THRESHOLD
            and win_rate > _PROMOTION_WIN_RATE_MIN
            and expectancy_r > _PROMOTION_EXPECTANCY_MIN
            and max_drawdown < _PROMOTION_DRAWDOWN_MAX
            and shadow_coverage >= 0.05
            and decay_severity not in {"ALERT", "CRITICAL"}
        )
        if ready:
            candidacy = "promotable"
        elif any(flag in warnings for flag in {"non_positive_expectancy", "drawdown_breach", "edge_decay_active", "quality_score_critical"}):
            candidacy = "reject-candidacy"
        else:
            candidacy = "hold"
        readiness_score = max(
            0.0,
            min(
                100.0,
                (quality_score * 0.45)
                + (min(trade_count, 60) / 60.0 * 20.0)
                + (shadow_coverage * 20.0)
                + (shadow_alignment * 15.0),
            ),
        )
        return {
            "strategy_id": strategy_id,
            "name": str(metadata.get("name", strategy_id)),
            "lifecycle_state": lifecycle_state,
            "linked_bots": bot_ids,
            "ready": ready,
            "candidacy": candidacy,
            "quality_tier": _quality_tier(quality_score),
            "trade_count": trade_count,
            "quality_score": round(quality_score, 2),
            "win_rate": round(win_rate, 2),
            "expectancy_r": round(expectancy_r, 4),
            "max_drawdown_pct": round(max_drawdown, 4),
            "confidence": confidence,
            "shadow_coverage": round(shadow_coverage, 4),
            "shadow_alignment": round(shadow_alignment, 4),
            "decay_severity": decay_severity,
            "decay_signals": decay_signals,
            "readiness_score": round(readiness_score, 2),
            "warnings": warnings,
            "generated_at": _iso_now(),
        }


def _match_signal(
    signal: dict[str, object],
    *,
    signal_sources: set[str],
    bot_ids: set[str],
    metadata: dict[str, object],
) -> tuple[bool, bool, bool]:
    source = str(signal.get("source", "unknown")).lower()
    lane_hint = str(signal.get("lane_hint", "") or "").lower()
    strategy_hint = str(signal.get("strategy_hint", "") or "").lower()
    bot_id = str(signal.get("bot_id", "") or "").lower()
    source_hit = bool(signal_sources) and any(candidate in {source, lane_hint, strategy_hint} for candidate in signal_sources)
    inferred_bot_id = bot_id or _infer_bot_id_from_signal(signal)
    bot_hit = bool(bot_ids) and inferred_bot_id in bot_ids
    matched = source_hit or bot_hit
    if not matched:
        return False, False, False
    min_confidence = _as_optional_float(metadata.get("min_confidence"))
    min_score = _as_optional_float(metadata.get("min_score_threshold"))
    confidence = _as_optional_float(signal.get("confidence"))
    score = _as_optional_float(signal.get("score"))
    honest_fallback = False
    if min_confidence is not None and confidence is None:
        honest_fallback = True
    if min_score is not None and score is None:
        honest_fallback = True
    confidence_ok = min_confidence is None or (confidence is not None and confidence >= min_confidence)
    score_ok = min_score is None or (score is not None and score >= min_score)
    return True, confidence_ok and score_ok, honest_fallback


def _infer_bot_id_from_signal(signal: dict[str, object]) -> str:
    joined = " ".join(
        [
            str(signal.get("source", "")),
            str(signal.get("lane_hint", "")),
            str(signal.get("strategy_hint", "")),
        ]
    ).lower()
    if any(token in joined for token in {"tradecopy", "copycat", "13f"}):
        return "copycat"
    if any(token in joined for token in {"options", "gambler"}):
        return "gambler"
    if any(token in joined for token in {"swingtrade", "drifter"}):
        return "drifter"
    if any(token in joined for token in {"ausmine", "nugget", "miner"}):
        return "nugget_bot"
    if any(token in joined for token in {"evo_catalyst", "evo", "volt"}):
        return "evo_catalyst"
    if any(token in joined for token in {"watchlist_momentum", "scout", "turbo"}):
        return "turbo"
    return ""


def _detect_edge_decay(trades: list[dict[str, object]], *, short_window: int = 20, long_window: int = 100) -> dict[str, object]:
    if len(trades) < short_window:
        return {
            "severity": "INSUFFICIENT_DATA",
            "signals": [],
            "short_window": short_window,
            "long_window": long_window,
            "message": f"Need at least {short_window} trades",
        }
    short = calculate_scorecard(trades[-short_window:], window=short_window)
    baseline = calculate_scorecard(trades[-min(long_window, len(trades)):], window=min(long_window, len(trades)))
    signals: list[dict[str, object]] = []
    short_wr = float(short.get("win_rate", 0.0) or 0.0)
    baseline_wr = float(baseline.get("win_rate", 0.0) or 0.0)
    if baseline_wr > 0.0 and (baseline_wr - short_wr) > 10.0:
        signals.append({"signal": "falling_hit_rate", "severity": "WARNING"})
    short_exp = float(short.get("expectancy_r", 0.0) or 0.0)
    baseline_exp = float(baseline.get("expectancy_r", 0.0) or 0.0)
    if baseline_exp > 0.0 and short_exp < (baseline_exp * 0.6):
        signals.append({"signal": "falling_expectancy", "severity": "ALERT" if short_exp < 0.0 else "WARNING"})
    if short_exp < 0.0 and len(trades) >= 30:
        signals.append({"signal": "negative_expectancy", "severity": "ALERT"})
    short_dd = float(short.get("max_drawdown_pct", 0.0) or 0.0)
    baseline_dd = float(baseline.get("max_drawdown_pct", 0.0) or 0.0)
    if baseline_dd > 0.0 and short_dd > (baseline_dd * 1.5) and short_dd > 10.0:
        signals.append({"signal": "drawdown_worsening", "severity": "WARNING"})
    if int(short.get("max_consecutive_losses", 0) or 0) >= 6:
        signals.append({"signal": "consecutive_losses", "severity": "WATCH"})
    if not signals:
        severity = "HEALTHY"
    elif any(item["severity"] == "ALERT" for item in signals):
        severity = "ALERT"
    elif len(signals) >= 2 or any(item["severity"] == "WARNING" for item in signals):
        severity = "WARNING"
    else:
        severity = "WATCH"
    return {
        "severity": severity,
        "signals": signals,
        "short": {
            "window": short_window,
            "win_rate": short.get("win_rate"),
            "expectancy_r": short.get("expectancy_r"),
            "max_drawdown_pct": short.get("max_drawdown_pct"),
        },
        "baseline": {
            "window": min(long_window, len(trades)),
            "win_rate": baseline.get("win_rate"),
            "expectancy_r": baseline.get("expectancy_r"),
            "max_drawdown_pct": baseline.get("max_drawdown_pct"),
        },
    }


def _trade_payload(row: TradeOutcomeRecord) -> dict[str, object]:
    features = row.features if isinstance(row.features, dict) else {}
    return {
        "outcome": row.outcome,
        "pnl_pct": row.pnl_pct,
        "executed_at": row.created_at.isoformat() if row.created_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "sl_pct": features.get("sl_pct"),
        "r_multiple": features.get("r_multiple"),
        "hold_hours": features.get("hold_hours"),
        "regime": features.get("regime"),
    }


def _bot_id(row: TradeOutcomeRecord) -> str:
    if row.bot_id is not None and row.bot_id.strip():
        return row.bot_id.strip()
    return "unknown"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _as_optional_float(value: object) -> float | None:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object, default: float) -> float:
    parsed = _as_optional_float(value)
    return default if parsed is None else parsed


def _as_int(value: object, default: int) -> int:
    try:
        return int(cast(float | int | str, value))
    except (TypeError, ValueError):
        return default


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _highest_decay_severity(values: list[str]) -> str:
    order = {
        "INSUFFICIENT_DATA": 0,
        "HEALTHY": 1,
        "WATCH": 2,
        "WARNING": 3,
        "ALERT": 4,
        "CRITICAL": 5,
    }
    if not values:
        return "INSUFFICIENT_DATA"
    return max(values, key=lambda item: order.get(item, 0))


def _worst_confidence(values: list[str]) -> str:
    order = {
        "very_low": 0,
        "low": 1,
        "moderate": 2,
        "good": 3,
        "high": 4,
    }
    if not values:
        return "very_low"
    return min(values, key=lambda item: order.get(item, 0))


def _quality_tier(score: float) -> str:
    if score >= 80.0:
        return "elite"
    if score >= 60.0:
        return "strong"
    if score >= 40.0:
        return "moderate"
    if score >= 20.0:
        return "weak"
    return "critical"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
