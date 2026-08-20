from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from typing import TYPE_CHECKING

from app.services.intelligence.service import IntelligenceService
from app.services.signals.service import SignalService
from db.models import SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import LaneRuntimeStatus, LaneScanState, OrderAction, SignalStatus
from domain.models.signal import Signal

if TYPE_CHECKING:
    from runtime.container import Container


OPTIONS_VERSION = "1.0.0"
MAX_PREMIUM_PER_TRADE = 500.0
MIN_PREMIUM_PER_TRADE = 50.0
MAX_PORTFOLIO_PCT = 0.15
MAX_CONCURRENT = 3
MIN_SIGNAL_SCORE = 80
TARGET_DTE_MIN = 30
TARGET_DTE_MAX = 60
TARGET_DELTA_MIN = 0.30
TARGET_DELTA_MAX = 0.45
PROFIT_TARGET_PCT = 1.00
STOP_LOSS_PCT = 0.50
MIN_DTE_EXIT = 14
MAX_HOLD_DAYS = 45
DEFAULT_EQUITY = 100_000.0
INDEX_HEDGES: frozenset[str] = frozenset({"SPY", "QQQ"})

OPTIONS_UNIVERSE: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "AMD",
    "INTC",
    "QCOM",
    "CRM",
    "ORCL",
    "ADBE",
    "JPM",
    "BAC",
    "GS",
    "XOM",
    "CVX",
    "UNH",
    "LLY",
    "PFE",
    "JNJ",
    "WMT",
    "COST",
    "HD",
    "NKE",
    "DIS",
    "NFLX",
    "SPY",
    "QQQ",
)


class OptionsLaneService:
    async def run_scan(self, *, container: "Container | None" = None) -> dict[str, object]:
        created_at = _iso_now()
        regime = await self._regime(container)
        equity = await self._equity(container)
        max_allocation = round(equity * MAX_PORTFOLIO_PCT, 2)
        open_positions = [row for row in await self._options_outcomes(limit=1000) if row.status == "open"]
        premium_deployed = round(sum(_premium_paid(row) for row in open_positions), 2)
        held_underlyings = {str(row.underlying).upper() for row in open_positions}
        skip_reasons: dict[str, int] = {}

        summary: dict[str, object] = {
            "status": "ok",
            "status_code": "ok",
            "scan_state": LaneScanState.COMPLETED.value,
            "regime": regime,
            "equity": round(equity, 2),
            "premium_deployed": premium_deployed,
            "max_allocation": max_allocation,
            "scanned": 0,
            "signals": 0,
            "skipped": 0,
            "scan_at": created_at,
            "candidates": [],
            "skip_reasons": skip_reasons,
        }

        if len(open_positions) >= MAX_CONCURRENT:
            summary["status"] = "MAX_POSITIONS"
            summary["status_code"] = "max_positions"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = f"max options ({MAX_CONCURRENT}) reached"
            await self._persist_summary(summary)
            return summary

        if premium_deployed >= max_allocation:
            summary["status"] = "ALLOCATION_FULL"
            summary["status_code"] = "allocation_full"
            summary["scan_state"] = LaneScanState.SKIPPED.value
            summary["gate_reason"] = f"options allocation full ({premium_deployed:.2f}/{max_allocation:.2f})"
            await self._persist_summary(summary)
            return summary

        pending_signals = await self._pending_signals(limit=500)
        candidates: list[dict[str, object]] = []
        for symbol in OPTIONS_UNIVERSE:
            if symbol in INDEX_HEDGES and regime not in {"BEAR", "CRISIS"}:
                _increment(skip_reasons, "index_hedge_disabled")
                continue

            summary["scanned"] = _as_int(summary.get("scanned")) + 1

            if symbol in held_underlyings:
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, "underlying_already_open")
                continue

            earnings = await IntelligenceService().earnings_check(symbol)
            if not bool(earnings.get("allowed", True)):
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, "earnings_block")
                continue

            evaluation = _evaluate_for_options(symbol=symbol, signals=pending_signals, regime=regime)
            if evaluation["action"] == "SKIP":
                reason_code = str(evaluation.get("reason_code", "signal_mismatch"))
                _increment(skip_reasons, reason_code)
                continue

            option_type = "call" if str(evaluation["action"]) == "CALL" else "put"
            contract = await self._best_contract(symbol=symbol, option_type=option_type, container=container)
            remaining_allocation = max_allocation - premium_deployed
            if _as_float(contract.get("premium")) > remaining_allocation:
                summary["skipped"] = _as_int(summary.get("skipped")) + 1
                _increment(skip_reasons, "allocation_remaining_too_small")
                continue

            candidates.append(
                {
                    "underlying": symbol,
                    "contract": contract["contract"],
                    "type": option_type,
                    "strike": contract["strike"],
                    "expiration": contract["expiration"],
                    "dte": contract["dte"],
                    "delta": contract["delta"],
                    "premium": contract["premium"],
                    "iv": contract["iv"],
                    "spot_price": contract["spot_price"],
                    "bid_price": contract.get("bid_price"),
                    "ask_price": contract.get("ask_price"),
                    "limit_price": contract.get("limit_price"),
                    "score": _as_int(evaluation.get("score")),
                    "confidence": _as_float(evaluation.get("confidence")),
                    "regime": regime,
                    "market": "options_us",
                    "order_type": "limit",
                    "reasons": _string_list(evaluation.get("reasons")),
                }
            )

        created_signals = await self._persist_signals(candidates)
        summary["signals"] = created_signals
        summary["candidates"] = candidates
        if not candidates:
            summary["status"] = "NO_CANDIDATES"
            summary["status_code"] = "no_candidates"
        await self._persist_summary(summary)
        return summary

    async def get_status(self, *, bot_id: str, enabled: bool, lifecycle_state: str) -> dict[str, object]:
        latest = await self._latest_payload("lane.scan.options")
        max_allocation = round(_as_float(_dict(latest).get("max_allocation")) or (DEFAULT_EQUITY * MAX_PORTFOLIO_PCT), 2)
        premium_deployed = 0.0
        outcomes = await self._options_outcomes(limit=1000)
        open_positions = [row for row in outcomes if row.status == "open"]
        closed_positions = [row for row in outcomes if row.status in {"win", "loss", "breakeven"}]
        positions: dict[str, dict[str, object]] = {}
        exit_watch: list[dict[str, object]] = []

        for row in open_positions:
            snapshot = await self._position_snapshot(row)
            premium_deployed += _as_float(snapshot.get("premium_paid"))
            contract = str(snapshot.get("contract", row.symbol))
            positions[contract] = snapshot
            exit_item = _exit_watch(snapshot)
            if exit_item is not None:
                exit_watch.append(exit_item)

        total_pnl = sum(_as_float(row.pnl_pct) for row in closed_positions)
        allocation_pct = round((premium_deployed / max_allocation) * 100.0, 2) if max_allocation > 0 else 0.0
        signals_generated = await self._signal_count()
        last_scan = None if latest is None else latest.get("scan_at")
        regime = None if latest is None else latest.get("regime")
        scan_status = None if latest is None else latest.get("status")
        scan_state = None if latest is None else latest.get("scan_state")
        scan_status_code = None if latest is None else latest.get("status_code")

        return {
            "version": OPTIONS_VERSION,
            "enabled": enabled,
            "last_scan": last_scan,
            "regime": regime,
            "scan_status": scan_status,
            "scan_state": scan_state,
            "scan_status_code": scan_status_code,
            "open_positions": len(open_positions),
            "max_positions": MAX_CONCURRENT,
            "premium_deployed": round(premium_deployed, 2),
            "max_allocation": max_allocation,
            "allocation_pct": allocation_pct,
            "positions": positions,
            "exit_watch": exit_watch,
            "stats": {
                "opened": len(outcomes),
                "closed": len(closed_positions),
                "total_pnl": round(total_pnl, 4),
            },
            "universe_size": len(OPTIONS_UNIVERSE),
            "config": {
                "min_score": MIN_SIGNAL_SCORE,
                "max_premium": int(MAX_PREMIUM_PER_TRADE),
                "dte_range": f"{TARGET_DTE_MIN}-{TARGET_DTE_MAX}",
                "delta_range": f"{TARGET_DELTA_MIN:.2f}-{TARGET_DELTA_MAX:.2f}",
                "profit_target": f"+{PROFIT_TARGET_PCT:.0%}",
                "stop_loss": f"-{STOP_LOSS_PCT:.0%}",
                "dte_exit": MIN_DTE_EXIT,
                "max_hold_days": MAX_HOLD_DAYS,
            },
            "lane": "options",
            "bot_id": bot_id,
            "status": LaneRuntimeStatus.ACTIVE.value if enabled else LaneRuntimeStatus.IDLE.value,
            "lifecycle_state": lifecycle_state,
            "fleet_slot_status": _fleet_slot_status(lifecycle_state),
            "signals_generated": signals_generated,
        }

    async def _pending_signals(self, *, limit: int) -> list[Signal]:
        try:
            async with UnitOfWork() as uow:
                return await SignalsRepository(connection=uow.connection).list_pending(limit=limit)
        except Exception:
            return []

    async def _persist_signals(self, candidates: list[dict[str, object]]) -> int:
        created = 0
        for candidate in candidates:
            underlying = str(candidate.get("underlying", "")).upper()
            contract = str(candidate.get("contract", "")).upper()
            if not underlying or not contract:
                continue
            signal_id = (
                f"options:{underlying}:"
                f"{hashlib.md5(_signal_fingerprint(candidate).encode('utf-8')).hexdigest()[:12]}"
            )
            if await self._signal_exists(signal_id):
                continue
            score = min(max(_as_float(candidate.get("score")) / 100.0, 0.0), 0.99)
            confidence = min(max(_as_float(candidate.get("confidence")) or score, 0.0), 0.95)
            limit_price = _as_float(candidate.get("limit_price")) or _as_float(candidate.get("ask_price"))
            option_position = {
                "underlying": underlying,
                "contract": contract,
                "type": str(candidate.get("type", "")),
                "strike": _as_float(candidate.get("strike")),
                "expiration": str(candidate.get("expiration", "")),
                "dte": _as_int(candidate.get("dte")),
                "delta": _as_float(candidate.get("delta")),
                "premium": _as_float(candidate.get("premium")),
                "iv": _as_float(candidate.get("iv")),
                "bid_price": _as_float(candidate.get("bid_price")),
                "ask_price": _as_float(candidate.get("ask_price")),
            }
            signal = Signal(
                signal_id=signal_id,
                symbol=contract,
                action=OrderAction.BUY,
                score=score,
                confidence=confidence,
                priority=8 if score >= 0.88 else 7,
                source="options",
                lane_hint="options",
                strategy_hint="options_premium",
                headline=(
                    f"Gambler {str(candidate.get('type', '')).upper()}: "
                    f"{underlying} ${_as_float(candidate.get('strike')):.2f} exp {candidate.get('expiration')}"
                )[:200],
                metadata={
                    "candidate": _json_payload(candidate),
                    "option_position": option_position,
                    "underlying_symbol": underlying,
                    "market": "options_us",
                    "order_type": "limit",
                    "limit_price": limit_price,
                    "reference_price": limit_price,
                    "entry_price": limit_price,
                    "max_risk": _as_float(candidate.get("premium")),
                    "exit_policy": {
                        "profit_target_pct": PROFIT_TARGET_PCT,
                        "stop_loss_pct": STOP_LOSS_PCT,
                        "dte_exit": MIN_DTE_EXIT,
                        "max_hold_days": MAX_HOLD_DAYS,
                    },
                    "size_policy": {
                        "max_premium_per_trade": MAX_PREMIUM_PER_TRADE,
                        "max_portfolio_pct": MAX_PORTFOLIO_PCT,
                        "max_concurrent": MAX_CONCURRENT,
                    },
                    "regime": str(candidate.get("regime", "NEUTRAL")).lower(),
                },
            )
            try:
                await SignalService().ingest_signal(signal)
            except Exception:
                continue
            await self._append_artifact(
                event_type="lane.candidate.options",
                payload={
                    "signal_id": signal_id,
                    "headline": signal.headline or "",
                    "candidate": _json_payload(candidate),
                    "created_at": _iso_now(),
                },
                actor="options",
            )
            created += 1
        return created

    async def _best_contract(
        self,
        *,
        symbol: str,
        option_type: str,
        container: "Container | None",
    ) -> dict[str, object]:
        real_contract = await self._real_contract_candidate(
            symbol=symbol,
            option_type=option_type,
            container=container,
        )
        if real_contract is not None:
            return real_contract

        spot_price = await self._spot_price(symbol=symbol, container=container)
        salt = f"{symbol}:{option_type}"
        dte = int(round(_stable_metric(salt, "dte", float(TARGET_DTE_MIN), float(TARGET_DTE_MAX))))
        expiration = (datetime.now(timezone.utc).date() + timedelta(days=dte)).isoformat()
        delta = round(_stable_metric(salt, "delta", TARGET_DELTA_MIN, TARGET_DELTA_MAX), 2)
        iv = round(_stable_metric(salt, "iv", 0.18, 0.52), 2)
        strike_offset = _stable_metric(salt, "strike_offset", 0.01, 0.06)
        if option_type == "call":
            strike = round(spot_price * (1.0 + strike_offset), 2)
        else:
            strike = round(spot_price * (1.0 - strike_offset), 2)
        contract = f"{symbol}_{expiration.replace('-', '')}_{option_type.upper()}_{int(round(strike * 100))}"
        premium = round(_stable_metric(contract, "premium", MIN_PREMIUM_PER_TRADE, MAX_PREMIUM_PER_TRADE), 2)
        return {
            "contract": contract,
            "spot_price": round(spot_price, 2),
            "strike": strike,
            "expiration": expiration,
            "dte": dte,
            "delta": delta,
            "premium": premium,
            "iv": iv,
            "bid_price": round(max(premium / 100.0 - 0.05, 0.01), 2),
            "ask_price": round(premium / 100.0, 2),
            "limit_price": round(premium / 100.0, 2),
        }

    async def _real_contract_candidate(
        self,
        *,
        symbol: str,
        option_type: str,
        container: "Container | None",
    ) -> dict[str, object] | None:
        if container is None:
            return None
        broker = getattr(container, "broker", None)
        get_option_chain = getattr(broker, "get_option_chain", None)
        if not callable(get_option_chain):
            return None

        now = datetime.now(timezone.utc).date()
        try:
            chain = await get_option_chain(
                symbol,
                expiration_date_gte=(now + timedelta(days=TARGET_DTE_MIN)).isoformat(),
                expiration_date_lte=(now + timedelta(days=TARGET_DTE_MAX)).isoformat(),
                option_type=option_type,
                limit=50,
            )
        except Exception:
            return None
        if not isinstance(chain, list) or not chain:
            return None

        spot_price = await self._spot_price(symbol=symbol, container=container)
        filtered: list[dict[str, object]] = []
        for contract in chain:
            if not isinstance(contract, dict):
                continue
            greeks = _dict(contract.get("greeks"))
            delta = abs(_as_float(greeks.get("delta")))
            ask_price = _as_float(contract.get("ask_price"))
            bid_price = _as_float(contract.get("bid_price"))
            last_price = _as_float(contract.get("last_price"))
            limit_price = ask_price or last_price or bid_price
            premium = round(limit_price * 100.0, 2)
            expiration = str(contract.get("expiration_date", ""))[:10]
            try:
                dte = (datetime.strptime(expiration, "%Y-%m-%d").date() - now).days
            except ValueError:
                dte = 0
            if not (TARGET_DTE_MIN <= dte <= TARGET_DTE_MAX):
                continue
            if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                continue
            if not (MIN_PREMIUM_PER_TRADE <= premium <= MAX_PREMIUM_PER_TRADE):
                continue
            filtered.append(
                {
                    "contract": str(contract.get("symbol", "")).upper(),
                    "spot_price": round(spot_price, 2),
                    "strike": _as_float(contract.get("strike_price")),
                    "expiration": expiration,
                    "dte": dte,
                    "delta": round(delta, 3),
                    "premium": premium,
                    "iv": round(_as_float(greeks.get("implied_volatility")), 4),
                    "bid_price": round(bid_price, 4),
                    "ask_price": round(ask_price, 4),
                    "limit_price": round(limit_price, 4),
                }
            )
        if not filtered:
            return None
        filtered.sort(key=lambda row: (abs(_as_float(row.get("delta")) - 0.35), _as_float(row.get("premium"))))
        return filtered[0]

    async def _spot_price(self, *, symbol: str, container: "Container | None") -> float:
        if container is None:
            return round(_stable_metric(symbol, "spot", 80.0, 420.0), 2)
        try:
            quote = await container.broker.get_quote(symbol)
        except Exception:
            return round(_stable_metric(symbol, "spot", 80.0, 420.0), 2)
        last = _as_float(quote.get("last"))
        if last > 0:
            return round(last, 2)
        return round(_stable_metric(symbol, "spot", 80.0, 420.0), 2)

    async def _regime(self, container: "Container | None") -> str:
        if container is None:
            return "NEUTRAL"
        try:
            snapshot = await IntelligenceService().regime_snapshot(container)
        except Exception:
            return "NEUTRAL"
        regime = str(snapshot.get("regime", "NEUTRAL")).upper()
        return regime if regime in {"BULL", "NEUTRAL", "BEAR", "CRISIS"} else "NEUTRAL"

    async def _equity(self, container: "Container | None") -> float:
        if container is None:
            return DEFAULT_EQUITY
        try:
            account = await container.broker.get_account()
        except Exception:
            return DEFAULT_EQUITY
        equity = _as_float(account.get("equity"))
        return equity if equity > 0 else DEFAULT_EQUITY

    async def _options_outcomes(self, *, limit: int) -> list["_OutcomeSnapshot"]:
        try:
            async with UnitOfWork() as uow:
                outcomes = await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        rows: list[_OutcomeSnapshot] = []
        for outcome in outcomes:
            bot_id = str(outcome.bot_id or "").lower()
            source = str(outcome.source or "").lower()
            if bot_id != "gambler" and source != "options":
                continue
            rows.append(
                _OutcomeSnapshot(
                    trade_id=outcome.trade_id,
                    signal_id=outcome.signal_id,
                    symbol=outcome.symbol,
                    status=outcome.outcome.value,
                    pnl_pct=_as_float(outcome.pnl_pct),
                    entry_price=outcome.entry_price,
                    exit_price=outcome.exit_price,
                    quantity=outcome.quantity,
                    opened_at=outcome.opened_at,
                    closed_at=outcome.closed_at,
                    close_reason=outcome.close_reason,
                    features=_dict(outcome.features),
                    underlying=_first_text(
                        _dict(outcome.features).get("underlying"),
                        _dict(_dict(outcome.features).get("option_position")).get("underlying"),
                        outcome.symbol,
                    ).upper(),
                )
            )
        return rows

    async def _position_snapshot(self, row: "_OutcomeSnapshot") -> dict[str, object]:
        features = dict(row.features)
        candidate: dict[str, object] = {}
        option_position: dict[str, object] = {}
        if "candidate" in features:
            candidate = {**candidate, **_dict(features.get("candidate"))}
        if "option_position" in features:
            option_position = {**option_position, **_dict(features.get("option_position"))}
        contract = _first_text(
            features.get("contract"),
            option_position.get("contract"),
            row.symbol,
        )
        underlying = _first_text(
            features.get("underlying"),
            option_position.get("underlying"),
            row.symbol,
        ).upper()
        quantity = _as_float(row.quantity)
        premium_paid = _as_float(features.get("premium_paid"))
        premium_per_contract = _as_float(option_position.get("premium"))
        if premium_paid <= 0 and premium_per_contract > 0 and quantity > 0:
            premium_paid = premium_per_contract * quantity
        if premium_paid <= 0 and row.entry_price is not None and quantity > 0:
            premium_paid = float(row.entry_price) * quantity * 100.0
        expiration = _first_text(
            features.get("expiration"),
            option_position.get("expiration"),
        )
        dte = _days_to_expiration(expiration)
        days_held = max(0, (datetime.now(timezone.utc) - row.opened_at).days)
        current_value = premium_paid * (1.0 + row.pnl_pct) if premium_paid > 0 else 0.0
        return {
            "contract": contract,
            "underlying": underlying,
            "type": _first_text(features.get("type"), option_position.get("type")).lower(),
            "strike": _as_float(features.get("strike")) or _as_float(option_position.get("strike")),
            "expiration": expiration,
            "qty": quantity,
            "premium_paid": round(premium_paid, 2),
            "entry_price": row.entry_price,
            "pnl_pct": round(row.pnl_pct, 4),
            "current_value": round(current_value, 2),
            "days_held": days_held,
            "dte": dte,
            "entered_at": row.opened_at.isoformat(),
            "close_reason": row.close_reason,
        }

    async def _signal_exists(self, signal_id: str) -> bool:
        try:
            row = await SignalRecord.filter(signal_id=signal_id).first()
        except Exception:
            return False
        return row is not None

    async def _signal_count(self) -> int:
        try:
            return int(await SignalRecord.filter(source="options").count())
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
        await self._append_artifact(event_type="lane.scan.options", payload=summary, actor="options")
        await self._append_outbox(
            event_type="OptionsScanCompleted",
            entity_key=f"options:{_iso_now()}",
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
        underlying: str,
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
        self.underlying = underlying


def _evaluate_for_options(*, symbol: str, signals: list[Signal], regime: str) -> dict[str, object]:
    result: dict[str, object] = {"action": "SKIP", "score": 0, "confidence": 0.0, "reasons": [], "reason_code": "no_signal"}
    symbol_signals = [signal for signal in signals if signal.symbol == symbol.upper()]
    if not symbol_signals:
        return result

    best = max(symbol_signals, key=_signal_score)
    score = _signal_score(best)
    confidence = _signal_confidence(best)
    result["score"] = score
    result["confidence"] = confidence

    if score < MIN_SIGNAL_SCORE:
        result["reasons"] = [f"Score {score} < {MIN_SIGNAL_SCORE}"]
        result["reason_code"] = "score_below_threshold"
        return result

    if best.action == OrderAction.BUY and regime in {"BULL", "NEUTRAL"}:
        result["action"] = "CALL"
        result["reasons"] = [f"Bullish signal ({score}) + {regime} regime"]
        if confidence > 0.7:
            result["score"] = min(100, score + 5)
            result["reasons"].append("High confidence bonus")
        result["reason_code"] = "call_entry"
        return result

    if regime in {"BEAR", "CRISIS"}:
        result["action"] = "PUT"
        result["reasons"] = [f"Bearish hedge - {regime} regime"]
        result["reason_code"] = "put_hedge"
        return result

    if best.action == OrderAction.SELL and score >= MIN_SIGNAL_SCORE:
        result["action"] = "PUT"
        result["reasons"] = [f"Bearish signal ({score})"]
        result["reason_code"] = "put_entry"
        return result

    result["reasons"] = ["signal did not satisfy options entry policy"]
    result["reason_code"] = "signal_mismatch"
    return result


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


def _signal_confidence(signal: Signal) -> float:
    if signal.confidence is not None:
        return float(signal.confidence)
    return max(0.0, min(signal.score, 0.95))


def _premium_paid(row: _OutcomeSnapshot) -> float:
    premium_paid = _as_float(row.features.get("premium_paid"))
    if premium_paid > 0:
        return premium_paid
    quantity = _as_float(row.quantity)
    if row.entry_price is not None and quantity > 0:
        return float(row.entry_price) * quantity * 100.0
    return 0.0


def _exit_watch(position: dict[str, object]) -> dict[str, object] | None:
    pnl_pct = _as_float(position.get("pnl_pct"))
    dte = _as_int(position.get("dte"))
    days_held = _as_int(position.get("days_held"))
    reason = None
    detail = ""
    if pnl_pct >= PROFIT_TARGET_PCT:
        reason = "take_profit"
        detail = f"+{pnl_pct:.0%}"
    elif pnl_pct <= -STOP_LOSS_PCT:
        reason = "stop_loss"
        detail = f"{pnl_pct:.0%}"
    elif dte > 0 and dte <= MIN_DTE_EXIT:
        reason = "dte_exit"
        detail = f"{dte} days left"
    elif days_held >= MAX_HOLD_DAYS:
        reason = "max_hold"
        detail = f"{days_held} days held"
    if reason is None:
        return None
    return {
        "contract": position.get("contract"),
        "underlying": position.get("underlying"),
        "reason": reason,
        "detail": detail,
        "pnl_pct": round(pnl_pct, 4),
        "dte": dte,
        "days_held": days_held,
    }


def _days_to_expiration(expiration: str | None) -> int:
    if not expiration:
        return 0
    try:
        exp_date = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    return (exp_date - datetime.now(timezone.utc).date()).days


def _signal_fingerprint(candidate: dict[str, object]) -> str:
    key_parts = (
        str(candidate.get("underlying", "")),
        str(candidate.get("contract", "")),
        str(candidate.get("type", "")),
        str(candidate.get("expiration", "")),
        f"{_as_float(candidate.get('strike')):.2f}",
        f"{_as_float(candidate.get('premium')):.2f}",
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


def _stable_metric(symbol: str, salt: str, min_value: float, max_value: float) -> float:
    raw = hashlib.md5(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    normalized = int(raw[:8], 16) / 0xFFFFFFFF
    return min_value + ((max_value - min_value) * normalized)


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


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
