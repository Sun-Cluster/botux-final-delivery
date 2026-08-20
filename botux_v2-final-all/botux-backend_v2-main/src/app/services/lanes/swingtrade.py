from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from typing import TYPE_CHECKING

from app.services.intelligence.service import IntelligenceService
from app.services.signals.service import SignalService
from db.models import SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import LaneRuntimeStatus, LaneScanState, OrderAction
from domain.models.signal import Signal

if TYPE_CHECKING:
    from runtime.container import Container


SWINGTRADE_VERSION = "2.0.0"
MIN_HAWK_SCORE = 55
MIN_RSI_PULLBACK = 28.0
MAX_RSI_PULLBACK = 50.0
MIN_VOLUME_RATIO = 1.0
MIN_ADX_TREND = 25.0
PARTIAL_PROFIT_PCT = 0.06
PARTIAL_SELL_RATIO = 0.5
TRAILING_STOP_PCT = 0.04
ATR_STOP_MULTIPLIER = 1.5
MAX_HOLD_DAYS = 12
BREAKEVEN_DAYS = 5
TIME_STOP_ATR_MULT = 1.0
MIN_POSITION_PCT = 0.01
MAX_POSITION_PCT = 0.03
MIN_POSITION_USD = 100.0
MAX_PER_SECTOR = 2
MAX_OPEN_SWINGS = 8
MAX_TECH_EXPOSURE = 0.30
MIN_EQUITY = 500.0
ETF_SYMBOLS: frozenset[str] = frozenset({"SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "GDX", "ARKK"})
TECH_SECTORS: frozenset[str] = frozenset({"TECH", "SEMI", "SOFTWARE"})
DEFAULT_EQUITY = 100_000.0
DEFAULT_MIN_BUYING_POWER = 100.0

DRIFTER_UNIVERSE: tuple[str, ...] = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "AMD", "INTC", "QCOM", "MU", "MRVL", "ON", "AMAT", "LRCX",
    "CRM", "ORCL", "ADBE", "NOW", "PLTR", "SNOW", "NET", "DDOG",
    "JPM", "BAC", "GS", "MS", "C", "WFC", "AXP", "BLK",
    "UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "BMY", "AMGN",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "VLO",
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS", "NFLX",
    "CAT", "DE", "BA", "GE", "RTX", "LMT", "UNP", "UPS",
    "FCX", "NEM", "AA", "CLF", "X", "VALE", "RIO", "BHP",
    "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "GDX", "ARKK",
)

SECTOR_MAP: dict[str, str] = {
    "AAPL": "TECH", "MSFT": "TECH", "GOOGL": "TECH", "AMZN": "TECH",
    "NVDA": "TECH", "META": "TECH", "TSLA": "TECH", "AVGO": "TECH",
    "AMD": "SEMI", "INTC": "SEMI", "QCOM": "SEMI", "MU": "SEMI",
    "MRVL": "SEMI", "ON": "SEMI", "AMAT": "SEMI", "LRCX": "SEMI",
    "CRM": "SOFTWARE", "ORCL": "SOFTWARE", "ADBE": "SOFTWARE", "NOW": "SOFTWARE",
    "PLTR": "SOFTWARE", "SNOW": "SOFTWARE", "NET": "SOFTWARE", "DDOG": "SOFTWARE",
    "JPM": "FINANCE", "BAC": "FINANCE", "GS": "FINANCE", "MS": "FINANCE",
    "C": "FINANCE", "WFC": "FINANCE", "AXP": "FINANCE", "BLK": "FINANCE",
    "UNH": "HEALTH", "JNJ": "HEALTH", "LLY": "HEALTH", "PFE": "HEALTH",
    "ABBV": "HEALTH", "MRK": "HEALTH", "BMY": "HEALTH", "AMGN": "HEALTH",
    "XOM": "ENERGY", "CVX": "ENERGY", "COP": "ENERGY", "SLB": "ENERGY",
    "EOG": "ENERGY", "OXY": "ENERGY", "MPC": "ENERGY", "VLO": "ENERGY",
    "WMT": "CONSUMER", "COST": "CONSUMER", "HD": "CONSUMER", "NKE": "CONSUMER",
    "SBUX": "CONSUMER", "MCD": "CONSUMER", "DIS": "CONSUMER", "NFLX": "CONSUMER",
    "CAT": "INDUSTRIAL", "DE": "INDUSTRIAL", "BA": "INDUSTRIAL", "GE": "INDUSTRIAL",
    "RTX": "INDUSTRIAL", "LMT": "INDUSTRIAL", "UNP": "INDUSTRIAL", "UPS": "INDUSTRIAL",
    "FCX": "MATERIALS", "NEM": "MATERIALS", "AA": "MATERIALS", "CLF": "MATERIALS",
    "X": "MATERIALS", "VALE": "MATERIALS", "RIO": "MATERIALS", "BHP": "MATERIALS",
}


class SwingtradeLaneService:
    async def run_scan(self, *, container: "Container | None" = None) -> dict[str, object]:
        created_at = _iso_now()
        regime, regime_multiplier = await self._regime_snapshot(container)
        capital_snapshot = await self._capital_snapshot(container)
        equity = capital_snapshot["equity"]
        buying_power = capital_snapshot["buying_power"]
        capital_base = capital_snapshot["capital_base"]
        open_positions = [row for row in await self._swing_outcomes(limit=1000) if row.status == "open"]
        held_symbols = {row.symbol for row in open_positions}
        skip_reasons: dict[str, int] = {}
        pending_signals = await self._pending_signals(limit=500)

        summary: dict[str, object] = {
            "status": "ok",
            "status_code": "ok",
            "scan_state": LaneScanState.COMPLETED.value,
            "regime": regime,
            "regime_multiplier": regime_multiplier,
            "equity": round(equity, 2),
            "buying_power": None if buying_power is None else round(buying_power, 2),
            "capital_base": round(capital_base, 2),
            "scanned": 0,
            "signals": 0,
            "skipped": 0,
            "errors": 0,
            "scan_at": created_at,
            "candidates": [],
            "skip_reasons": skip_reasons,
        }

        if regime == "CRISIS":
            summary["status"] = "CRISIS_SKIP"
            summary["status_code"] = "crisis_skip"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = "crisis regime - no scanning"
            await self._persist_summary(summary)
            return summary

        if len(open_positions) >= MAX_OPEN_SWINGS:
            summary["status"] = "MAX_POSITIONS"
            summary["status_code"] = "max_positions"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = f"max swing positions ({MAX_OPEN_SWINGS}) reached"
            await self._persist_summary(summary)
            return summary

        if equity < MIN_EQUITY:
            summary["status"] = "LOW_EQUITY"
            summary["status_code"] = "low_equity"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = f"equity below minimum ({equity:.2f} < {MIN_EQUITY:.2f})"
            await self._persist_summary(summary)
            return summary

        if capital_snapshot["scan_allowed"] is False:
            summary["status"] = "NO_BUYING_POWER"
            summary["status_code"] = "insufficient_buying_power"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = str(capital_snapshot["gate_reason"] or "buying power unavailable")
            await self._persist_summary(summary)
            return summary

        candidates: list[dict[str, object]] = []
        for symbol in DRIFTER_UNIVERSE:
            if symbol in ETF_SYMBOLS:
                _increment(skip_reasons, "regime_etf_only")
                continue
            if symbol in held_symbols:
                _increment(skip_reasons, "already_in_position")
                continue

            summary["scanned"] = _as_int(summary.get("scanned")) + 1

            if not _check_sector_limits(symbol=symbol, open_positions=open_positions):
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, "sector_limit")
                continue

            earnings = await IntelligenceService().earnings_check(symbol)
            if not bool(earnings.get("allowed", True)):
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, "earnings_block")
                continue

            try:
                analysis = await self._analyse_stock(
                    symbol=symbol,
                    container=container,
                    pending_signals=pending_signals,
                    regime_multiplier=regime_multiplier,
                    capital_base=capital_base,
                )
            except Exception:
                summary["errors"] = _as_int(summary.get("errors")) + 1
                _increment(skip_reasons, "analysis_error")
                continue

            if str(analysis["action"]) != "BUY":
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, str(analysis.get("reason_code", "below_threshold")))
                continue

            candidates.append(
                {
                    "symbol": symbol,
                    "score": _as_int(analysis.get("score")),
                    "rsi": round(_as_float(analysis["rsi"]), 1),
                    "volume_ratio": round(_as_float(analysis["volume_ratio"]), 2),
                    "weekly_trend": str(analysis["weekly_trend"]),
                    "entry_price": round(_as_float(analysis["entry_price"]), 2),
                    "stop_loss": round(_as_float(analysis["stop_loss"]), 2),
                    "take_profit": round(_as_float(analysis["take_profit"]), 2),
                    "atr": round(_as_float(analysis["atr"]), 2),
                    "adx": round(_as_float(analysis["adx"]), 1),
                    "size_usd": round(_as_float(analysis["size_usd"]), 2),
                    "qty": _as_int(analysis.get("qty")),
                    "sector": _sector(symbol),
                    "regime": regime,
                    "market": "us_equities",
                    "order_type": "bracket",
                    "reasons": _string_list(analysis.get("reasons")),
                }
            )

        candidates.sort(key=lambda row: (int(row["score"]), _as_float(row["volume_ratio"])), reverse=True)
        created_signals = await self._persist_signals(candidates)
        summary["signals"] = created_signals
        summary["candidates"] = candidates
        if not candidates:
            summary["status"] = "NO_CANDIDATES"
            summary["status_code"] = "no_candidates"
        await self._persist_summary(summary)
        return summary

    async def get_status(self, *, bot_id: str, enabled: bool, lifecycle_state: str) -> dict[str, object]:
        latest = await self._latest_payload("lane.scan.swingtrade")
        outcomes = await self._swing_outcomes(limit=1000)
        open_positions = [row for row in outcomes if row.status == "open"]
        closed_positions = [row for row in outcomes if row.status in {"win", "loss", "breakeven"}]
        positions: dict[str, dict[str, object]] = {}
        exit_watch: list[dict[str, object]] = []
        for row in open_positions:
            snapshot = await self._position_snapshot(row)
            positions[row.symbol] = snapshot
            exit_item = _exit_watch(snapshot)
            if exit_item is not None:
                exit_watch.append(exit_item)

        total_pnl = sum(_as_float(row.pnl_pct) for row in closed_positions)
        candidates = [] if latest is None else latest.get("candidates", [])
        return {
            "version": SWINGTRADE_VERSION,
            "enabled": enabled,
            "last_scan": None if latest is None else latest.get("scan_at"),
            "scan_status": None if latest is None else latest.get("status"),
            "scan_state": None if latest is None else latest.get("scan_state"),
            "scan_status_code": None if latest is None else latest.get("status_code"),
            "regime": None if latest is None else latest.get("regime"),
            "open_positions": len(open_positions),
            "max_positions": MAX_OPEN_SWINGS,
            "positions": positions,
            "exit_watch": exit_watch,
            "scan_candidates": len(candidates) if isinstance(candidates, list) else 0,
            "stats": {
                "opened": len(outcomes),
                "closed": len(closed_positions),
                "total_pnl": round(total_pnl, 4),
            },
            "universe_size": len(DRIFTER_UNIVERSE),
            "sector_exposure": _sector_exposure(open_positions),
            "config": {
                "min_score": MIN_HAWK_SCORE,
                "partial_at": f"+{PARTIAL_PROFIT_PCT:.0%}",
                "trailing_stop": f"{TRAILING_STOP_PCT:.0%}",
                "atr_multiplier": ATR_STOP_MULTIPLIER,
                "max_hold_days": MAX_HOLD_DAYS,
                "breakeven_days": BREAKEVEN_DAYS,
                "time_stop_atr_mult": TIME_STOP_ATR_MULT,
                "sizing": f"{MIN_POSITION_PCT:.1%}-{MAX_POSITION_PCT:.1%}",
                "max_per_sector": MAX_PER_SECTOR,
            },
            "lane": "swingtrade",
            "bot_id": bot_id,
            "status": LaneRuntimeStatus.ACTIVE.value if enabled else LaneRuntimeStatus.IDLE.value,
            "lifecycle_state": lifecycle_state,
            "fleet_slot_status": _fleet_slot_status(lifecycle_state),
            "signals_generated": await self._signal_count(),
        }

    async def _analyse_stock(
        self,
        *,
        symbol: str,
        container: "Container | None",
        pending_signals: list[Signal],
        regime_multiplier: float,
        capital_base: float,
    ) -> dict[str, object]:
        price = await self._price(symbol=symbol, container=container)
        ema20 = price * (1.0 - _stable_metric(symbol, "ema20_offset", -0.015, 0.03))
        ema50 = price * (1.0 - _stable_metric(symbol, "ema50_offset", -0.03, 0.06))
        rsi = round(_stable_metric(symbol, "rsi", 24.0, 69.0), 1)
        volume_ratio = round(_stable_metric(symbol, "volume_ratio", 0.75, 2.0), 2)
        atr = round(max(0.5, price * _stable_metric(symbol, "atr_pct", 0.012, 0.055)), 2)
        adx = round(_stable_metric(symbol, "adx", 14.0, 42.0), 1)
        momentum_5d = round(_stable_metric(symbol, "momentum_5d", -6.0, 8.0), 2)
        hawk_signal = _best_hawk_signal(symbol=symbol, signals=pending_signals)
        score = 0
        reasons: list[str] = []
        weekly_trend = "FLAT"

        if price > ema20 and ema20 > ema50:
            weekly_trend = "STRONG_UP"
            score += 25
        elif price > ema20:
            weekly_trend = "UP"
            score += 15
        elif price < ema20 and ema20 < ema50:
            weekly_trend = "STRONG_DOWN"
            return _analysis_skip(
                symbol=symbol,
                price=price,
                atr=atr,
                rsi=rsi,
                volume_ratio=volume_ratio,
                adx=adx,
                weekly_trend=weekly_trend,
                reasons=["Strong downtrend"],
                reason_code="strong_downtrend",
                score=score - 20,
            )
        else:
            score += 5

        if MIN_RSI_PULLBACK <= rsi <= MAX_RSI_PULLBACK:
            score += 20
            reasons.append(f"RSI pullback {rsi:.0f}")
        elif rsi < MIN_RSI_PULLBACK:
            score -= 10
            reasons.append(f"RSI too low ({rsi:.0f})")
        elif 50.0 < rsi <= 60.0:
            score += 5
            reasons.append(f"RSI mild pullback ({rsi:.0f})")
        elif rsi > 65.0:
            return _analysis_skip(
                symbol=symbol,
                price=price,
                atr=atr,
                rsi=rsi,
                volume_ratio=volume_ratio,
                adx=adx,
                weekly_trend=weekly_trend,
                reasons=[f"RSI overbought ({rsi:.0f})"],
                reason_code="rsi_overbought",
                score=score - 15,
            )

        if volume_ratio >= MIN_VOLUME_RATIO:
            score += 15
            reasons.append(f"Volume {volume_ratio:.1f}x avg")
        else:
            score -= 5
            reasons.append(f"Low volume ({volume_ratio:.1f}x)")

        near_ema20 = abs(price - ema20) / price <= 0.012 if price > 0 else False
        if adx > 30.0 and near_ema20 and rsi < 35.0:
            score += 25
            reasons.append(f"Holy grail ADX={adx:.0f}")
        elif adx >= 30.0:
            score += 10
            reasons.append(f"Strong trend ADX={adx:.0f}")
        elif adx >= MIN_ADX_TREND:
            score += 5
        elif adx < 15.0:
            score -= 5

        stop_loss = round(price - (atr * ATR_STOP_MULTIPLIER), 2)
        stop_pct = ((price - stop_loss) / price) if price > 0 else 0.0
        if stop_pct > 0.08:
            score -= 10
            reasons.append(f"ATR stop too wide ({stop_pct:.1%})")
        take_profit = round(price + ((price - stop_loss) * 2.5), 2)

        if price > ema20 and price > ema50:
            score += 10
        elif price < ema50:
            score -= 10

        if -3.0 < momentum_5d < 0.0:
            score += 10
            reasons.append(f"5d pullback {momentum_5d:.1f}%")
        elif momentum_5d > 5.0:
            score -= 5
            reasons.append(f"Extended 5d momentum {momentum_5d:.1f}%")
        elif momentum_5d < -5.0:
            score -= 10
            reasons.append(f"Falling knife {momentum_5d:.1f}%")

        if hawk_signal is not None:
            hawk_score = _signal_score(hawk_signal)
            bonus = min(18, max(0, hawk_score - MIN_HAWK_SCORE) // 2)
            score += int(bonus)
            reasons.append(f"Hawk score {hawk_score}")

        size_usd = _position_size(score=score, equity=capital_base, regime_multiplier=regime_multiplier)
        qty = max(1, int(size_usd / price)) if price > 0 else 0

        if score >= MIN_HAWK_SCORE and qty > 0:
            reasons.append(f"ENTRY signal score={score}")
            return {
                "symbol": symbol,
                "action": "BUY",
                "score": score,
                "reasons": reasons,
                "entry_price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "atr": atr,
                "adx": adx,
                "rsi": rsi,
                "volume_ratio": volume_ratio,
                "weekly_trend": weekly_trend,
                "size_usd": round(size_usd, 2),
                "qty": qty,
            }

        reasons.append(f"Below threshold ({score}/{MIN_HAWK_SCORE})")
        return _analysis_skip(
            symbol=symbol,
            price=price,
            atr=atr,
            rsi=rsi,
            volume_ratio=volume_ratio,
            adx=adx,
            weekly_trend=weekly_trend,
            reasons=reasons,
            reason_code="below_threshold",
            score=score,
        )

    async def _price(self, *, symbol: str, container: "Container | None") -> float:
        if container is None:
            return round(_stable_metric(symbol, "price", 25.0, 320.0), 2)
        try:
            quote = await container.broker.get_quote(symbol)
        except Exception:
            return round(_stable_metric(symbol, "price", 25.0, 320.0), 2)
        last = _as_float(quote.get("last"))
        return round(last if last > 0 else _stable_metric(symbol, "price", 25.0, 320.0), 2)

    async def _pending_signals(self, *, limit: int) -> list[Signal]:
        try:
            async with UnitOfWork() as uow:
                return await SignalsRepository(connection=uow.connection).list_pending(limit=limit)
        except Exception:
            return []

    async def _regime_snapshot(self, container: "Container | None") -> tuple[str, float]:
        if container is None:
            return "NEUTRAL", 0.65
        try:
            snapshot = await IntelligenceService().regime_snapshot(container)
        except Exception:
            return "NEUTRAL", 0.65
        regime = str(snapshot.get("regime", "NEUTRAL")).upper()
        multiplier = _as_float(snapshot.get("multiplier")) or 0.65
        if regime not in {"BULL", "NEUTRAL", "BEAR", "CRISIS"}:
            return "NEUTRAL", multiplier
        return regime, multiplier

    async def _equity(self, container: "Container | None") -> float:
        if container is None:
            return DEFAULT_EQUITY
        try:
            account = await container.broker.get_account()
        except Exception:
            return DEFAULT_EQUITY
        equity = _as_float(account.get("equity"))
        return equity if equity > 0 else DEFAULT_EQUITY

    async def _capital_snapshot(self, container: "Container | None") -> dict[str, object]:
        equity = await self._equity(container)
        if container is None:
            return {
                "equity": equity,
                "buying_power": None,
                "capital_base": equity,
                "scan_allowed": True,
                "gate_reason": None,
            }
        try:
            account = await container.broker.get_account()
        except Exception:
            return {
                "equity": equity,
                "buying_power": None,
                "capital_base": equity,
                "scan_allowed": True,
                "gate_reason": None,
            }

        policy = await self._execution_policy()
        buying_power = _as_float(account.get("buying_power"))
        capital_basis = str(policy.get("capital_basis", "buying_power")).strip().lower()
        min_buying_power = max(_as_float(policy.get("min_buying_power_usd")) or DEFAULT_MIN_BUYING_POWER, 0.0)
        skip_on_low_buying_power = bool(policy.get("skip_scan_when_insufficient_buying_power", True))

        capital_base = equity
        if capital_basis == "buying_power" and buying_power is not None:
            capital_base = max(buying_power, 0.0)

        buying_power_short = (
            capital_basis == "buying_power"
            and buying_power is not None
            and buying_power < min_buying_power
        )
        if buying_power_short and skip_on_low_buying_power:
            available = 0.0 if buying_power is None else buying_power
            return {
                "equity": equity,
                "buying_power": buying_power,
                "capital_base": capital_base,
                "scan_allowed": False,
                "gate_reason": (
                    f"buying power below minimum ({available:.2f} < {min_buying_power:.2f})"
                ),
            }

        if capital_base <= 0:
            capital_base = equity

        return {
            "equity": equity,
            "buying_power": buying_power,
            "capital_base": capital_base,
            "scan_allowed": True,
            "gate_reason": None,
        }

    async def _execution_policy(self) -> dict[str, object]:
        try:
            async with UnitOfWork() as uow:
                profile = await BotsRepository(connection=uow.connection).get_bot_profile("drifter")
        except Exception:
            profile = None
        if not isinstance(profile, dict):
            return {
                "capital_basis": "buying_power",
                "min_buying_power_usd": DEFAULT_MIN_BUYING_POWER,
                "skip_scan_when_insufficient_buying_power": True,
            }
        raw_policy = profile.get("execution_policy")
        if not isinstance(raw_policy, dict):
            return {
                "capital_basis": "buying_power",
                "min_buying_power_usd": DEFAULT_MIN_BUYING_POWER,
                "skip_scan_when_insufficient_buying_power": True,
            }
        return raw_policy

    async def _persist_signals(self, candidates: list[dict[str, object]]) -> int:
        created = 0
        for candidate in candidates:
            symbol = str(candidate.get("symbol", "")).upper()
            if not symbol:
                continue
            signal_id = (
                f"swingtrade:{symbol}:"
                f"{hashlib.md5(_signal_fingerprint(candidate).encode('utf-8')).hexdigest()[:12]}"
            )
            if await self._signal_exists(signal_id):
                continue
            score = min(max(_as_float(candidate.get("score")) / 100.0, 0.0), 0.99)
            confidence = min(max(score, 0.58), 0.95)
            signal = Signal(
                signal_id=signal_id,
                symbol=symbol,
                action=OrderAction.BUY,
                score=score,
                confidence=confidence,
                priority=7 if score >= 0.75 else 6,
                source="swingtrade",
                lane_hint="swingtrade",
                strategy_hint="swing_momentum",
                headline=f"Drifter swing entry: {symbol}"[:200],
                metadata={
                    "candidate": _json_payload(candidate),
                    "market": "us_equities",
                    "order_type": "bracket",
                    "trade_type": "swing",
                    "risk_policy": {
                        "partial_profit_pct": PARTIAL_PROFIT_PCT,
                        "partial_sell_ratio": PARTIAL_SELL_RATIO,
                        "trailing_stop_pct": TRAILING_STOP_PCT,
                        "atr_stop_multiplier": ATR_STOP_MULTIPLIER,
                        "breakeven_days": BREAKEVEN_DAYS,
                        "time_stop_atr_mult": TIME_STOP_ATR_MULT,
                        "max_hold_days": MAX_HOLD_DAYS,
                    },
                    "size_policy": {
                        "min_position_pct": MIN_POSITION_PCT,
                        "max_position_pct": MAX_POSITION_PCT,
                        "min_position_usd": MIN_POSITION_USD,
                    },
                    "sector": str(candidate.get("sector", "OTHER")),
                    "regime": str(candidate.get("regime", "NEUTRAL")).lower(),
                },
            )
            try:
                await SignalService().ingest_signal(signal)
            except Exception:
                continue
            await self._append_artifact(
                event_type="lane.candidate.swingtrade",
                payload={
                    "signal_id": signal_id,
                    "headline": signal.headline or "",
                    "candidate": _json_payload(candidate),
                    "created_at": _iso_now(),
                },
                actor="swingtrade",
            )
            created += 1
        return created

    async def _swing_outcomes(self, *, limit: int) -> list["_OutcomeSnapshot"]:
        try:
            async with UnitOfWork() as uow:
                outcomes = await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        rows: list[_OutcomeSnapshot] = []
        for outcome in outcomes:
            bot_id = str(outcome.bot_id or "").lower()
            source = str(outcome.source or "").lower()
            if bot_id != "drifter" and source != "swingtrade":
                continue
            rows.append(
                _OutcomeSnapshot(
                    trade_id=outcome.trade_id,
                    signal_id=outcome.signal_id,
                    symbol=outcome.symbol.upper(),
                    status=outcome.outcome.value,
                    pnl_pct=_as_float(outcome.pnl_pct),
                    entry_price=outcome.entry_price,
                    exit_price=outcome.exit_price,
                    quantity=outcome.quantity,
                    opened_at=outcome.opened_at,
                    closed_at=outcome.closed_at,
                    close_reason=outcome.close_reason,
                    features=_dict(outcome.features),
                )
            )
        return rows

    async def _position_snapshot(self, row: "_OutcomeSnapshot") -> dict[str, object]:
        features = dict(row.features)
        qty = _as_float(row.quantity)
        entry_price = _as_float(row.entry_price)
        stop_loss = _as_float(features.get("stop_loss"))
        take_profit = _as_float(features.get("take_profit"))
        trailing_stop = _optional_float(features.get("trailing_stop"))
        partial_filled = bool(features.get("partial_filled", False))
        atr = _as_float(features.get("atr"))
        current_price = entry_price * (1.0 + row.pnl_pct) if entry_price > 0 else entry_price
        days_held = max(0, (datetime.now(timezone.utc) - row.opened_at).days)
        if partial_filled and trailing_stop is None and current_price > 0:
            trailing_stop = round(current_price * (1.0 - TRAILING_STOP_PCT), 2)
        return {
            "entry": round(entry_price, 2),
            "stop": round(stop_loss, 2),
            "target": round(take_profit, 2),
            "qty": qty,
            "partial_filled": partial_filled,
            "trailing_stop": trailing_stop,
            "current_price": round(current_price, 2),
            "pnl_pct": round(row.pnl_pct, 4),
            "atr": round(atr, 2),
            "sector": _sector(row.symbol),
            "days_held": days_held,
            "entered_at": row.opened_at.isoformat(),
        }

    async def _signal_exists(self, signal_id: str) -> bool:
        try:
            row = await SignalRecord.filter(signal_id=signal_id).first()
        except Exception:
            return False
        return row is not None

    async def _signal_count(self) -> int:
        try:
            return int(await SignalRecord.filter(source="swingtrade").count())
        except Exception:
            return 0

    async def _latest_payload(self, event_type: str) -> dict[str, object] | None:
        try:
            async with UnitOfWork() as uow:
                row = await AuditLogsRepository(connection=uow.connection).latest_by_type(event_type=event_type)
        except Exception:
            return None
        if row is None or not isinstance(row.payload, dict):
            return None
        return {str(key): value for key, value in row.payload.items()}

    async def _persist_summary(self, payload: dict[str, object]) -> None:
        summary = _json_payload(payload)
        await self._append_artifact(event_type="lane.scan.swingtrade", payload=summary, actor="swingtrade")
        await self._append_outbox(
            event_type="SwingtradeScanCompleted",
            entity_key=f"swingtrade:{_iso_now()}",
            payload=summary,
        )

    async def _append_artifact(self, *, event_type: str, payload: dict[str, JSONValue], actor: str) -> None:
        try:
            async with UnitOfWork() as uow:
                await AuditLogsRepository(connection=uow.connection).append(
                    event_type=event_type,
                    payload=payload,
                    actor=actor,
                )
        except Exception:
            return

    async def _append_outbox(self, *, event_type: str, entity_key: str, payload: dict[str, JSONValue]) -> None:
        try:
            async with UnitOfWork() as uow:
                await append_outbox_event(
                    event_type=event_type,
                    entity_key=entity_key,
                    payload=payload,
                    connection=uow.connection,
                )
        except Exception:
            return


class _OutcomeSnapshot:
    def __init__(
        self,
        *,
        trade_id: str,
        signal_id: str,
        symbol: str,
        status: str,
        pnl_pct: float,
        entry_price: float | None,
        exit_price: float | None,
        quantity: float | None,
        opened_at: datetime,
        closed_at: datetime | None,
        close_reason: str | None,
        features: dict[str, object],
    ) -> None:
        self.trade_id = trade_id
        self.signal_id = signal_id
        self.symbol = symbol
        self.status = status
        self.pnl_pct = pnl_pct
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.quantity = quantity
        self.opened_at = opened_at
        self.closed_at = closed_at
        self.close_reason = close_reason
        self.features = features


def _analysis_skip(
    *,
    symbol: str,
    price: float,
    atr: float,
    rsi: float,
    volume_ratio: float,
    adx: float,
    weekly_trend: str,
    reasons: list[str],
    reason_code: str,
    score: int,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "action": "SKIP",
        "score": score,
        "reasons": reasons,
        "reason_code": reason_code,
        "entry_price": price,
        "stop_loss": round(price - (atr * ATR_STOP_MULTIPLIER), 2),
        "take_profit": round(price + (atr * ATR_STOP_MULTIPLIER * 2.5), 2),
        "atr": atr,
        "adx": adx,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "weekly_trend": weekly_trend,
        "size_usd": 0.0,
        "qty": 0,
    }


def _best_hawk_signal(*, symbol: str, signals: list[Signal]) -> Signal | None:
    matches = [signal for signal in signals if signal.symbol == symbol.upper()]
    if not matches:
        return None
    return max(matches, key=_signal_score)


def _signal_score(signal: Signal) -> int:
    metadata = _dict(signal.metadata)
    raw = metadata.get("ml_score")
    if raw is None:
        raw = metadata.get("sentiment")
    if raw is None:
        raw = signal.score * 100.0
    value = _as_float(raw)
    if 0.0 <= value <= 1.0:
        value *= 100.0
    return int(round(value))


def _position_size(*, score: int, equity: float, regime_multiplier: float) -> float:
    if equity <= 0:
        return 0.0
    pct = MIN_POSITION_PCT + (MAX_POSITION_PCT - MIN_POSITION_PCT) * min(1.0, max(0.0, (score - MIN_HAWK_SCORE) / 25.0))
    pct *= max(regime_multiplier, 0.0)
    size_usd = equity * pct
    return max(MIN_POSITION_USD, round(size_usd, 2))


def _check_sector_limits(*, symbol: str, open_positions: list[_OutcomeSnapshot]) -> bool:
    sector = _sector(symbol)
    sector_count = sum(1 for row in open_positions if _sector(row.symbol) == sector)
    if sector_count >= MAX_PER_SECTOR:
        return False
    if sector in TECH_SECTORS:
        tech_count = sum(1 for row in open_positions if _sector(row.symbol) in TECH_SECTORS)
        if tech_count >= int(MAX_OPEN_SWINGS * MAX_TECH_EXPOSURE):
            return False
    return True


def _sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.upper(), "OTHER")


def _sector_exposure(open_positions: list[_OutcomeSnapshot]) -> dict[str, int]:
    exposure: dict[str, int] = {}
    for row in open_positions:
        sector = _sector(row.symbol)
        exposure[sector] = int(exposure.get(sector, 0)) + 1
    return exposure


def _exit_watch(position: dict[str, object]) -> dict[str, object] | None:
    current_price = _as_float(position.get("current_price"))
    stop = _as_float(position.get("stop"))
    trailing_stop = _optional_float(position.get("trailing_stop"))
    pnl_pct = _as_float(position.get("pnl_pct"))
    days_held = _as_int(position.get("days_held"))
    partial_filled = bool(position.get("partial_filled"))
    atr = _as_float(position.get("atr"))
    entry = _as_float(position.get("entry"))
    if current_price <= stop and stop > 0:
        return {"reason": "stop_loss", "pnl_pct": round(pnl_pct, 4), "days_held": days_held}
    if pnl_pct >= PARTIAL_PROFIT_PCT and not partial_filled:
        return {"reason": "take_profit", "pnl_pct": round(pnl_pct, 4), "days_held": days_held}
    if trailing_stop is not None and current_price <= trailing_stop:
        return {"reason": "trailing_stop", "pnl_pct": round(pnl_pct, 4), "days_held": days_held}
    if days_held >= BREAKEVEN_DAYS and pnl_pct < 0.02 and not partial_filled:
        threshold_price = entry + (atr * TIME_STOP_ATR_MULT) if entry > 0 and atr > 0 else entry
        if current_price < threshold_price:
            return {"reason": "breakeven_exit", "pnl_pct": round(pnl_pct, 4), "days_held": days_held}
    if days_held >= MAX_HOLD_DAYS:
        return {"reason": "max_hold", "pnl_pct": round(pnl_pct, 4), "days_held": days_held}
    return None


def _signal_fingerprint(candidate: dict[str, object]) -> str:
    key_parts = (
        str(candidate.get("symbol", "")),
        str(candidate.get("weekly_trend", "")),
        f"{_as_float(candidate.get('entry_price')):.2f}",
        f"{_as_float(candidate.get('stop_loss')):.2f}",
        f"{_as_float(candidate.get('take_profit')):.2f}",
        f"{_as_float(candidate.get('score')):.2f}",
    )
    return "|".join(key_parts)


def _fleet_slot_status(lifecycle_state: str) -> str:
    normalized = lifecycle_state.strip().lower()
    if normalized in {"shadow", "paper"}:
        return "paper_only"
    if normalized in {"live", "scaled"}:
        return "active"
    if normalized in {"offline", "suspended"}:
        return "suspended"
    if normalized == "retired":
        return "retired"
    return "candidate"


def _dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _json_payload(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _stable_metric(symbol: str, salt: str, min_value: float, max_value: float) -> float:
    raw = hashlib.md5(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    normalized = int(raw[:8], 16) / 0xFFFFFFFF
    return min_value + ((max_value - min_value) * normalized)


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
