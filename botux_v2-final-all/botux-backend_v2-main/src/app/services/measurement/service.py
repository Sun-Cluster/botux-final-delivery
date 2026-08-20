from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TypedDict, cast

from db.models import CouncilDecisionRecord, SignalRecord, TradeOutcomeRecord
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork

SOURCE_FAMILIES = {
    "alpaca_news": "company_news",
    "newsapi": "company_news",
    "google_news": "company_news",
    "rss_multi": "company_news",
    "newsfeed_intel": "company_news",
    "ausmine": "gov_permits",
    "ausmine_intel": "gov_permits",
    "nugget_permit": "gov_permits",
    "scout": "scanner_technical",
    "scout_engine": "scanner_technical",
    "tradecopy": "institutional",
    "options_flow": "options_microstructure",
    "options": "options_microstructure",
    "swingtrade": "technical",
    "evo_catalyst": "event_catalyst",
    "evo_intel": "event_catalyst",
    "evo_quality": "event_catalyst",
    "ml_engine": "ml_signal",
}


class _ScorecardSummary(TypedDict, total=False):
    formula_version: str
    total_trades: int
    opened_trades: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    expectancy_usd: float | None
    expectancy_r: float | None
    profit_factor: float | None
    gross_profit: float
    gross_loss: float
    max_drawdown_pct: float | None
    sharpe: float | None
    regime_score: float
    consistency_score: float
    max_consecutive_losses: int
    avg_hold_hours: float
    confidence: str
    confidence_factor: float
    edge_status: str
    quality_score: float
    window: int
    suppressed: bool
    suppression_reason: str | None
    calculated_at: str


class _FamilyBucket(TypedDict):
    trades: int
    wins: int
    losses: int
    total_pnl: float
    raw_sources: set[str]


class _FamilyRow(TypedDict):
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    trust_score: float
    raw_sources: list[str]
    raw_source_count: int


class _SignalQualityRow(TypedDict, total=False):
    source: str
    total_signals: int
    council_approved: int
    council_rejected: int
    executed: int
    true_positives: int
    false_positives: int
    conversion_rate: float
    approval_rate: float
    false_positive_rate: float
    signal_quality: str


class _RankedBotRow(TypedDict, total=False):
    bot_id: str
    quality_score: float
    expectancy_r: float | None
    win_rate: float | None
    edge_status: str
    confidence: str
    total_trades: int


TradePayload = dict[str, object]


class MeasurementService:
    formula_version = "scorecard.v1"

    async def scorecards_by_bot(self, *, limit: int = 1000, window: int = 50) -> dict[str, _ScorecardSummary]:
        rows = await self._closed_outcome_rows(limit=limit)
        by_bot: dict[str, list[TradePayload]] = defaultdict(list)
        for row in rows:
            by_bot[_bot_id(row)].append(_trade_payload(row))
        return {bot_id: calculate_scorecard(trades, window=window) for bot_id, trades in by_bot.items()}

    async def minimal_scorecards_by_bot(
        self,
        *,
        days: int = 7,
        limit: int = 2000,
    ) -> dict[str, _ScorecardSummary]:
        safe_days = max(1, int(days))
        safe_limit = max(100, int(limit))
        since = datetime.now(timezone.utc) - timedelta(days=safe_days)
        closed_rows = await self._closed_outcome_rows(limit=safe_limit)
        recent_closed = [row for row in closed_rows if _row_recent_closed(row, since=since)]
        recent_opened = await self._recent_outcome_rows(since=since, limit=safe_limit)
        opened_by_bot: defaultdict[str, int] = defaultdict(int)
        for row in recent_opened:
            opened_by_bot[_bot_id(row)] += 1
        closed_by_bot: dict[str, list[TradePayload]] = defaultdict(list)
        for row in recent_closed:
            closed_by_bot[_bot_id(row)].append(_trade_payload(row))
        bot_ids = sorted(set(opened_by_bot.keys()) | set(closed_by_bot.keys()))
        result: dict[str, _ScorecardSummary] = {}
        for bot_id in bot_ids:
            closed_trades = closed_by_bot.get(bot_id, [])
            scorecard = calculate_scorecard(closed_trades, window=max(len(closed_trades), 1))
            scorecard["opened_trades"] = int(opened_by_bot.get(bot_id, 0))
            scorecard["closed_trades"] = int(len(closed_trades))
            scorecard["total_trades"] = int(len(closed_trades))
            scorecard["window"] = safe_days
            result[bot_id] = scorecard
        return result

    async def ranked_bots(self, *, limit: int = 1000, window: int = 50) -> list[_RankedBotRow]:
        scorecards = await self.scorecards_by_bot(limit=limit, window=window)
        rows: list[_RankedBotRow] = [
            {
                "bot_id": bot_id,
                "quality_score": card.get("quality_score", 0),
                "expectancy_r": card.get("expectancy_r"),
                "win_rate": card.get("win_rate"),
                "edge_status": str(card.get("edge_status") or "INSUFFICIENT_DATA"),
                "confidence": str(card.get("confidence") or "very_low"),
                "total_trades": card.get("total_trades", 0),
            }
            for bot_id, card in scorecards.items()
        ]
        rows.sort(key=lambda item: _as_float(item.get("quality_score")), reverse=True)
        return rows

    async def source_scoreboard(self, *, limit: int = 1000) -> dict[str, object]:
        rows = await self._closed_outcome_rows(limit=limit)
        families: defaultdict[str, _FamilyBucket] = defaultdict(
            lambda: {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "raw_sources": set()}
        )
        for row in rows:
            raw_source = _source(row)
            family = normalize_source_family(raw_source)
            bucket = families[family]
            bucket["trades"] += 1
            if row.outcome == "win":
                bucket["wins"] += 1
            elif row.outcome == "loss":
                bucket["losses"] += 1
            bucket["total_pnl"] += _as_float(row.pnl_pct)
            bucket["raw_sources"].add(raw_source)

        result: dict[str, _FamilyRow] = {}
        for family, stats in families.items():
            wins = stats["wins"]
            losses = stats["losses"]
            total = wins + losses
            raw_sources = stats["raw_sources"]
            result[family] = {
                "trades": stats["trades"],
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / total * 100, 1) if total else 0.0,
                "total_pnl": round(stats["total_pnl"], 4),
                "trust_score": _trust_score(wins=wins, losses=losses, pnl=stats["total_pnl"]),
                "raw_sources": sorted(raw_sources),
                "raw_source_count": len(raw_sources),
            }
        return {
            "formula_version": "source_trust.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "families": result,
            "total_families": len(result),
        }

    async def signal_quality_report(self, *, window: int = 1000) -> dict[str, object]:
        signals = (
            await SignalRecord.all()
            .prefetch_related("outcomes", "council_decisions")
            .order_by("-created_at")
            .limit(window)
        )
        by_source: dict[str, _SignalQualityRow] = {}
        for signal in signals:
            source = signal.source or "unknown"
            row = by_source.setdefault(
                source,
                {
                    "source": source,
                    "total_signals": 0,
                    "council_approved": 0,
                    "council_rejected": 0,
                    "executed": 0,
                    "true_positives": 0,
                    "false_positives": 0,
                },
            )
            row["total_signals"] += 1
            decisions = _signal_decisions(signal)
            if any(decision.decision == "approve" for decision in decisions):
                row["council_approved"] += 1
            if any(decision.decision in {"reject", "veto"} for decision in decisions):
                row["council_rejected"] += 1
            closed_outcomes = [outcome for outcome in _signal_outcomes(signal) if outcome.outcome in {"win", "loss"}]
            if signal.status == "executed" or closed_outcomes:
                row["executed"] += 1
            if any(outcome.outcome == "win" for outcome in closed_outcomes):
                row["true_positives"] += 1
            if any(outcome.outcome == "loss" for outcome in closed_outcomes):
                row["false_positives"] += 1

        rows: list[_SignalQualityRow] = []
        for row in by_source.values():
            total = row["total_signals"]
            voted = row["council_approved"] + row["council_rejected"]
            closed = row["true_positives"] + row["false_positives"]
            false_positive_rate = row["false_positives"] / closed * 100 if closed else 0.0
            row["conversion_rate"] = round(row["executed"] / max(total, 1) * 100, 1)
            row["approval_rate"] = round(row["council_approved"] / max(voted, 1) * 100, 1)
            row["false_positive_rate"] = round(false_positive_rate, 1)
            row["signal_quality"] = _signal_quality(false_positive_rate=false_positive_rate, closed=closed)
            rows.append(row)
        rows.sort(key=lambda item: (-item["total_signals"], item["source"]))
        return {
            "formula_version": "signal_quality.v1",
            "sources": rows,
            "count": len(rows),
            "window_size": len(signals),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _closed_outcome_rows(self, *, limit: int) -> list[TradeOutcomeRecord]:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            return await repo.list_closed_rows(limit=limit)

    async def _recent_outcome_rows(self, *, since: datetime, limit: int) -> list[TradeOutcomeRecord]:
        return (
            await TradeOutcomeRecord.filter(created_at__gte=since)
            .prefetch_related("signal")
            .order_by("-created_at")
            .limit(limit)
        )


def calculate_scorecard(trades: list[TradePayload], *, window: int = 50) -> _ScorecardSummary:
    recent = [_trade for _trade in trades[-window:] if _valid_trade(_trade)]
    total = len(recent)
    if total == 0:
        return _empty_scorecard(window=window)
    wins = [trade for trade in recent if _is_win(trade)]
    losses = [trade for trade in recent if not _is_win(trade)]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total * 100
    win_pnls = [_pnl(trade) for trade in wins]
    loss_pnls = [abs(_pnl(trade)) for trade in losses]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
    expectancy = (win_rate / 100.0 * avg_win) - ((100.0 - win_rate) / 100.0 * avg_loss)
    gross_profit = sum(win_pnls)
    gross_loss = sum(loss_pnls)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    r_multiples = [r_value for trade in recent if (r_value := _r_multiple(trade)) is not None]
    expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
    drawdown = _max_drawdown(recent)
    sharpe = _sharpe(recent)
    confidence = _confidence(total)
    regime_score = _regime_component(recent)
    consistency_score = _consistency_component(recent)
    quality_score = _quality_score(expectancy_r, profit_factor, win_rate, drawdown, regime_score, consistency_score, total)
    return {
        "formula_version": MeasurementService.formula_version,
        "total_trades": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "expectancy_usd": round(expectancy, 4),
        "expectancy_r": round(expectancy_r, 4),
        "profit_factor": round(profit_factor, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "max_drawdown_pct": round(drawdown, 4),
        "sharpe": round(sharpe, 4),
        "regime_score": round(regime_score, 2),
        "consistency_score": round(consistency_score, 2),
        "max_consecutive_losses": _max_consecutive_losses(recent),
        "avg_hold_hours": round(_avg_hold_hours(recent), 3),
        "confidence": confidence,
        "confidence_factor": _confidence_factor(total),
        "edge_status": _edge_status(win_rate, expectancy_r or expectancy, drawdown, total),
        "quality_score": round(quality_score, 2),
        "window": window,
        "suppressed": total < 10,
        "suppression_reason": f"Only {total} trades - metrics suppressed until >= 10" if total < 10 else None,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_source_family(raw_source: str) -> str:
    raw = raw_source.lower().strip()
    if not raw:
        return "unknown"
    if raw in SOURCE_FAMILIES:
        return SOURCE_FAMILIES[raw]
    for key, family in SOURCE_FAMILIES.items():
        if key in raw or raw in key:
            return family
    return "other"


def _trade_payload(row: TradeOutcomeRecord) -> TradePayload:
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


def _row_recent_closed(row: TradeOutcomeRecord, *, since: datetime) -> bool:
    closed_at = row.closed_at or row.created_at
    if closed_at is None:
        return False
    return closed_at >= since


def _source(row: TradeOutcomeRecord) -> str:
    if row.source is not None and row.source.strip():
        return row.source.strip()
    if row.signal is not None and row.signal.source:
        return row.signal.source
    return "unknown"


def _valid_trade(trade: TradePayload) -> bool:
    try:
        pnl = _as_float(trade.get("pnl_pct"))
    except ValueError:
        return False
    return -100.0 <= pnl <= 500.0 and str(trade.get("outcome")) in {"win", "loss", "breakeven"}


def _is_win(trade: TradePayload) -> bool:
    if trade.get("outcome") == "win":
        return True
    if trade.get("outcome") == "loss":
        return False
    return _pnl(trade) > 0


def _pnl(trade: TradePayload) -> float:
    try:
        return _as_float(trade.get("pnl_pct"))
    except ValueError:
        return 0.0


def _r_multiple(trade: TradePayload) -> float | None:
    explicit = trade.get("r_multiple")
    if explicit is not None:
        try:
            return _as_float(explicit)
        except ValueError:
            return None
    sl_pct = trade.get("sl_pct")
    try:
        sl = _as_float(sl_pct)
    except ValueError:
        return None
    if sl <= 0:
        return None
    return _pnl(trade) / sl


def _max_drawdown(trades: list[TradePayload]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        cumulative += _pnl(trade)
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


def _sharpe(trades: list[TradePayload]) -> float:
    returns = [_pnl(trade) for trade in trades]
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(min(252, len(returns) * 12))


def _max_consecutive_losses(trades: list[TradePayload]) -> int:
    current = 0
    max_streak = 0
    for trade in trades:
        if _is_win(trade):
            current = 0
        else:
            current += 1
            max_streak = max(max_streak, current)
    return max_streak


def _avg_hold_hours(trades: list[TradePayload]) -> float:
    values: list[float] = []
    for trade in trades:
        explicit = trade.get("hold_hours")
        if explicit is not None:
            try:
                values.append(_as_float(explicit))
                continue
            except ValueError:
                pass
        opened = _parse_dt(trade.get("executed_at"))
        closed = _parse_dt(trade.get("closed_at"))
        if opened is not None and closed is not None and closed > opened:
            values.append((closed - opened).total_seconds() / 3600.0)
    return sum(values) / len(values) if values else 0.0


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _confidence(total: int) -> str:
    if total < 10:
        return "very_low"
    if total < 30:
        return "low"
    if total < 50:
        return "moderate"
    if total < 100:
        return "good"
    return "high"


def _confidence_factor(total: int) -> float:
    if total < 10:
        return 0.3
    if total < 30:
        return 0.5
    if total < 50:
        return 0.7
    if total < 100:
        return 0.85
    return 1.0


def _edge_status(win_rate: float, expectancy: float, max_dd: float, total: int) -> str:
    if total < 10:
        return "INSUFFICIENT_DATA"
    if expectancy <= 0:
        return "CRITICAL" if total >= 30 else "WATCH"
    if max_dd > 15:
        return "ALERT"
    if win_rate < 40:
        return "WARNING"
    if win_rate >= 50 and max_dd < 10:
        return "HEALTHY"
    return "WATCH"


def _quality_score(
    expectancy_r: float,
    profit_factor: float,
    win_rate: float,
    max_dd: float,
    regime_score: float,
    consistency_score: float,
    total: int,
) -> float:
    expectancy_component = 100 if expectancy_r > 1.5 else 80 if expectancy_r > 1.0 else 60 if expectancy_r > 0.5 else 40 if expectancy_r > 0.1 else 20 if expectancy_r > 0 else 0
    profit_component = 100 if profit_factor > 2.5 else 85 if profit_factor > 2.0 else 70 if profit_factor > 1.5 else 50 if profit_factor > 1.2 else 30 if profit_factor > 1.0 else 0
    win_component = 100 if win_rate > 60 else 85 if win_rate > 55 else 70 if win_rate > 50 else 50 if win_rate > 45 else 30 if win_rate > 40 else 0
    drawdown_component = 100 if max_dd < 5 else 85 if max_dd < 8 else 65 if max_dd < 12 else 40 if max_dd < 15 else 20 if max_dd < 20 else 0
    return (
        expectancy_component * 0.30
        + profit_component * 0.20
        + win_component * 0.15
        + drawdown_component * 0.15
        + regime_score * 0.10
        + consistency_score * 0.10
    ) * _confidence_factor(total)


def _regime_component(trades: list[TradePayload]) -> float:
    by_regime: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        regime = str(trade.get("regime") or "unknown").upper()
        by_regime[regime].append(_pnl(trade))
    if not by_regime:
        return 0.0
    positive_regimes = 0
    neutral_regimes = 0
    negative_regimes = 0
    for pnl_values in by_regime.values():
        average = sum(pnl_values) / len(pnl_values)
        if average > 0.15:
            positive_regimes += 1
        elif average < -0.15:
            negative_regimes += 1
        else:
            neutral_regimes += 1
    if positive_regimes >= 2:
        return 100.0
    if positive_regimes >= 1 and negative_regimes == 0:
        return 75.0
    if positive_regimes >= 1 and neutral_regimes >= 1:
        return 60.0
    if positive_regimes >= 1 and negative_regimes >= 1:
        return 30.0
    return 0.0


def _consistency_component(trades: list[TradePayload]) -> float:
    monthly_returns: dict[str, float] = defaultdict(float)
    for trade in trades:
        closed_at = _parse_dt(trade.get("closed_at"))
        if closed_at is None:
            continue
        monthly_returns[closed_at.strftime("%Y-%m")] += _pnl(trade)
    if not monthly_returns:
        return 0.0
    last_six = sorted(monthly_returns.items(), key=lambda item: item[0], reverse=True)[:6]
    positive_months = sum(1 for _, pnl in last_six if pnl > 0)
    if positive_months >= 5:
        return 100.0
    if positive_months == 4:
        return 75.0
    if positive_months == 3:
        return 50.0
    if positive_months == 2:
        return 25.0
    return 0.0


def _trust_score(*, wins: int, losses: int, pnl: float) -> float:
    total = wins + losses
    if total == 0:
        return 0.0
    win_rate = wins / total
    pnl_component = max(min((pnl + 20.0) / 40.0, 1.0), 0.0)
    sample_component = min(total / 30.0, 1.0)
    return round((win_rate * 0.5 + pnl_component * 0.3 + sample_component * 0.2) * 100.0, 2)


def _signal_quality(*, false_positive_rate: float, closed: int) -> str:
    if closed < 10:
        return "insufficient_data"
    if false_positive_rate > 60:
        return "poor"
    if false_positive_rate > 45:
        return "weak"
    if false_positive_rate > 30:
        return "moderate"
    if false_positive_rate > 15:
        return "good"
    return "excellent"


def _empty_scorecard(*, window: int) -> _ScorecardSummary:
    return {
        "formula_version": MeasurementService.formula_version,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_win": None,
        "avg_loss": None,
        "expectancy_usd": None,
        "expectancy_r": None,
        "profit_factor": None,
        "gross_profit": 0,
        "gross_loss": 0,
        "max_drawdown_pct": None,
        "sharpe": None,
        "regime_score": 0,
        "consistency_score": 0,
        "max_consecutive_losses": 0,
        "avg_hold_hours": 0,
        "confidence": "very_low",
        "confidence_factor": 0.3,
        "edge_status": "INSUFFICIENT_DATA",
        "quality_score": 0,
        "window": window,
        "suppressed": False,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


def _signal_decisions(signal: SignalRecord) -> list[CouncilDecisionRecord]:
    decisions = getattr(signal, "council_decisions", ())
    return list(cast(list[CouncilDecisionRecord], decisions))


def _signal_outcomes(signal: SignalRecord) -> list[TradeOutcomeRecord]:
    outcomes = getattr(signal, "outcomes", ())
    return list(cast(list[TradeOutcomeRecord], outcomes))


def _as_float(value: object) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise ValueError(f"Cannot coerce to float: {value!r}")
