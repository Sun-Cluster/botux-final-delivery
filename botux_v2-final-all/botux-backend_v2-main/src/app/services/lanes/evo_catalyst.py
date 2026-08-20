from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from typing import TYPE_CHECKING

from app.services.market.data import MarketDataService
from app.services.signals.service import SignalService
from db.models import SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import LaneRuntimeStatus, LaneScanState, OrderAction
from domain.models.signal import Signal

if TYPE_CHECKING:
    from runtime.container import Container


EVO_CATALYST_VERSION = "2.0.0"
EVO_SIGNAL_SOURCE = "evo_catalyst"
EVO_STRATEGY_HINT = "evo_catalyst_event"
LOOKBACK_HOURS = 18
RECENT_SIGNAL_LOOKBACK_HOURS = 12
MAX_NEW_SIGNALS_PER_SCAN = 3
MAX_OPEN_POSITIONS = 3
MIN_CATALYST_SCORE = 68
MIN_SUPPORT_SIGNALS = 1
ETF_STOP_LOSS_PCT = 0.06
EQUITY_STOP_LOSS_PCT = 0.08
ETF_TAKE_PROFIT_PCT = 0.12
EQUITY_TAKE_PROFIT_PCT = 0.16

EVO_WATCHLIST: dict[str, dict[str, object]] = {
    "WDS.AX": {
        "market": "asx_equities",
        "theme": "energy_transition",
        "aliases": ("woodside", "lng", "gas", "energy"),
        "keywords": ("lng", "gas", "energy transition", "export", "approval"),
        "kind": "equity",
    },
    "MIN.AX": {
        "market": "asx_equities",
        "theme": "critical_minerals",
        "aliases": ("mineral resources", "minres", "lithium", "iron ore"),
        "keywords": ("lithium", "iron ore", "critical minerals", "battery metals", "approval"),
        "kind": "equity",
    },
    "LYC.AX": {
        "market": "asx_equities",
        "theme": "strategic_supply",
        "aliases": ("lynas", "rare earth", "rare earths"),
        "keywords": ("rare earth", "magnet", "strategic supply", "separation", "processing"),
        "kind": "equity",
    },
    "IGO.AX": {
        "market": "asx_equities",
        "theme": "battery_metals",
        "aliases": ("igo", "nickel", "lithium", "copper"),
        "keywords": ("nickel", "lithium", "battery metals", "copper", "joint venture"),
        "kind": "equity",
    },
    "PLS.AX": {
        "market": "asx_equities",
        "theme": "battery_metals",
        "aliases": ("pilbara", "pilbara minerals", "lithium"),
        "keywords": ("lithium", "battery metals", "spodumene", "offtake", "processing"),
        "kind": "equity",
    },
    "LIT": {
        "market": "us_equities",
        "theme": "energy_transition",
        "aliases": ("global x lithium", "lithium etf", "battery etf", "lithium"),
        "keywords": ("lithium", "battery metals", "etf", "energy transition", "miners"),
        "kind": "etf",
    },
}

EVO_THEMES: tuple[str, ...] = tuple(
    sorted({str(profile["theme"]) for profile in EVO_WATCHLIST.values() if isinstance(profile.get("theme"), str)})
)
SOURCE_WEIGHTS: dict[str, int] = {
    "asx_announcement": 18,
    "ausmine": 16,
    "alpaca_news": 11,
    "newsapi": 9,
    "gnews": 8,
    "google_news": 8,
}
POSITIVE_KEYWORDS: tuple[str, ...] = (
    "approval",
    "granted",
    "binding",
    "offtake",
    "expansion",
    "supply agreement",
    "strategic",
    "partnership",
    "production",
    "commissioning",
    "upgraded",
    "breakthrough",
    "rally",
    "surge",
    "support package",
    "subsidy",
)
NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "downgrade",
    "halt",
    "delay",
    "suspension",
    "dilution",
    "capital raise",
    "cost blowout",
    "lawsuit",
    "investigation",
    "miss",
    "cut guidance",
    "recall",
    "strike",
)


class EvoCatalystLaneService:
    async def run_scan(self, *, container: "Container | None" = None) -> dict[str, object]:
        created_at = _iso_now()
        now = datetime.now(timezone.utc)
        source_events = await self._source_events(now=now)
        recent_lane_signals = await self._recent_lane_signals(limit=500)
        outcomes = await self._evo_outcomes(limit=1000)
        open_symbols = {str(row["symbol"]).upper() for row in outcomes if str(row["outcome"]) == "open"}
        pending_symbols = await self._pending_symbols(limit=300)
        grouped_events = _group_events_by_symbol(source_events)
        blocked_reasons: dict[str, int] = {}
        candidates: list[dict[str, object]] = []

        if len(open_symbols) >= MAX_OPEN_POSITIONS:
            summary = {
                "status": "MAX_POSITIONS",
                "status_code": "max_positions",
                "scan_state": LaneScanState.SKIPPED.value,
                "scanned": len(source_events),
                "signals": 0,
                "candidates": [],
                "blocked_reasons": {"max_open_positions": len(open_symbols)},
                "open_positions": len(open_symbols),
                "watchlist": list(EVO_WATCHLIST.keys()),
                "scan_at": created_at,
                "source_events": source_events[:25],
            }
            await self._persist_summary(summary)
            return summary

        for symbol, events in grouped_events.items():
            if symbol in open_symbols:
                _increment(blocked_reasons, "already_open_position")
                continue
            if symbol in pending_symbols:
                _increment(blocked_reasons, "pending_signal")
                continue
            if _is_recent_duplicate(symbol=symbol, signals=recent_lane_signals, now=now):
                _increment(blocked_reasons, "recent_duplicate")
                continue
            market_snapshot = await self._market_snapshot(symbol=symbol, container=container)
            candidate = _build_candidate(
                symbol=symbol,
                events=events,
                market_snapshot=market_snapshot,
                created_at=created_at,
            )
            if candidate is None:
                _increment(blocked_reasons, _candidate_block_reason(symbol=symbol, events=events, market_snapshot=market_snapshot))
                continue
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                _as_int(item.get("score")),
                _as_int(item.get("support_signals")),
                _as_float(item.get("confidence")),
            ),
            reverse=True,
        )
        created_signals = await self._persist_signals(candidates[:MAX_NEW_SIGNALS_PER_SCAN])
        summary: dict[str, object] = {
            "status": "ok" if candidates else "NO_CANDIDATES",
            "status_code": "ok" if candidates else "no_candidates",
            "scan_state": LaneScanState.COMPLETED.value,
            "scanned": len(source_events),
            "signals": created_signals,
            "candidates": candidates,
            "blocked_reasons": blocked_reasons,
            "blocked": sum(blocked_reasons.values()),
            "open_positions": len(open_symbols),
            "themes": list(EVO_THEMES),
            "watchlist": list(EVO_WATCHLIST.keys()),
            "scan_at": created_at,
            "source_events": source_events[:25],
        }
        await self._persist_summary(summary)
        return summary

    async def get_status(self, *, bot_id: str, enabled: bool, lifecycle_state: str) -> dict[str, object]:
        latest = await self._latest_payload("lane.scan.evo_catalyst")
        outcomes = await self._evo_outcomes(limit=1000)
        open_positions = [row for row in outcomes if str(row["outcome"]) == "open"]
        closed_positions = [row for row in outcomes if str(row["outcome"]) in {"win", "loss", "breakeven"}]
        total_pnl = sum(_as_float(row.get("pnl_pct")) for row in closed_positions)
        signals_generated = await self._signal_count()
        return {
            "version": EVO_CATALYST_VERSION,
            "enabled": enabled,
            "lane": "evo_catalyst",
            "bot_id": bot_id,
            "status": LaneRuntimeStatus.ACTIVE.value if enabled else LaneRuntimeStatus.IDLE.value,
            "lifecycle_state": lifecycle_state,
            "fleet_slot_status": _fleet_slot_status(lifecycle_state),
            "watchlist": {"symbols": list(EVO_WATCHLIST.keys()), "count": len(EVO_WATCHLIST)},
            "open_positions": len(open_positions),
            "positions": {
                str(row["symbol"]): {
                    "entry": _as_float(row.get("entry_price")),
                    "qty": _as_float(row.get("quantity")),
                    "entered": str(row.get("opened_at", ""))[:10],
                    "trade_id": row.get("trade_id"),
                    "market": row.get("market"),
                }
                for row in open_positions
            },
            "stats": {
                "opened": len(outcomes),
                "closed": len(closed_positions),
                "total_pnl": round(total_pnl, 4),
            },
            "themes": list(EVO_THEMES),
            "signals_generated": signals_generated,
            "scan_state": None if latest is None else latest.get("scan_state"),
            "scan_status_code": None if latest is None else latest.get("status_code"),
            "last_scan": None if latest is None else latest.get("scan_at"),
            "blocked_reasons": {} if latest is None else _dict(latest.get("blocked_reasons")),
            "scan_candidates": 0 if latest is None else len(_list_dicts(latest.get("candidates"))),
        }

    async def _persist_signals(self, candidates: list[dict[str, object]]) -> int:
        created = 0
        for candidate in candidates:
            symbol = str(candidate.get("symbol", "")).upper()
            if not symbol:
                continue
            signal_id = (
                f"evo_catalyst:{symbol}:"
                f"{hashlib.md5(_signal_fingerprint(candidate).encode('utf-8')).hexdigest()[:12]}"
            )
            if await self._signal_exists(signal_id):
                continue
            score = _as_float(candidate.get("score")) / 100.0
            confidence = _as_float(candidate.get("confidence"))
            signal = Signal(
                signal_id=signal_id,
                symbol=symbol,
                action=OrderAction.BUY,
                score=round(min(max(score, 0.0), 0.99), 4),
                confidence=round(min(max(confidence, 0.55), 0.97), 4),
                priority=8 if score >= 0.8 else 7 if score >= 0.72 else 6,
                source=EVO_SIGNAL_SOURCE,
                lane_hint=EVO_SIGNAL_SOURCE,
                strategy_hint=EVO_STRATEGY_HINT,
                headline=str(candidate.get("headline", f"EVO catalyst long setup: {symbol}"))[:200],
                metadata={
                    "candidate": _json_payload(candidate),
                    "theme": str(candidate.get("theme", "energy_transition")),
                    "support_signals": _as_int(candidate.get("support_signals")),
                    "raw_score": _as_int(candidate.get("score")),
                    "market": str(candidate.get("market", _market_for_symbol(symbol))),
                    "order_type": "market",
                    "reference_price": _as_float(candidate.get("reference_price")),
                    "position_pct": _as_float(candidate.get("position_pct")),
                    "stop_loss_pct": _as_float(candidate.get("stop_loss_pct")),
                    "take_profit_pct": _as_float(candidate.get("take_profit_pct")),
                    "execution_bot_id": "evo_catalyst",
                    "origin_bot_id": "evo_catalyst_lane",
                    "event_ids": _string_list(candidate.get("event_ids"))[:8],
                    "event_sources": _string_list(candidate.get("event_sources"))[:8],
                    "reasons": _string_list(candidate.get("reasons"))[:8],
                },
            )
            try:
                await SignalService().ingest_signal(signal)
            except Exception:
                continue
            await self._append_artifact(
                event_type="lane.candidate.evo_catalyst",
                payload={
                    "signal_id": signal_id,
                    "headline": signal.headline or "",
                    "candidate": _json_payload(candidate),
                    "created_at": _iso_now(),
                },
                actor=EVO_SIGNAL_SOURCE,
            )
            created += 1
        return created

    async def _source_events(self, *, now: datetime) -> list[dict[str, object]]:
        cutoff = now - timedelta(hours=LOOKBACK_HOURS)
        events: list[dict[str, object]] = []
        seen_keys: set[str] = set()
        try:
            async with UnitOfWork() as uow:
                rows = await AuditLogsRepository(connection=uow.connection).list_recent_by_prefix(
                    prefix="news.article",
                    limit=300,
                )
        except Exception:
            rows = []
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else None
            if payload is None:
                continue
            event = _audit_payload_to_event(payload=payload, created_at=row.created_at.isoformat())
            if event is None:
                continue
            event_created = _parse_dt(str(event.get("created_at", "")))
            if event_created < cutoff:
                continue
            if not _event_intersects_watchlist(event):
                continue
            dedup_key = f"{event['event_id']}:{','.join(_string_list(event.get('symbols')))}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            events.append(event)

        try:
            async with UnitOfWork() as uow:
                rows = await SignalsRepository(connection=uow.connection).list_recent(limit=300)
        except Exception:
            rows = []
        for row in rows:
            if row.created_at < cutoff:
                continue
            if row.source == EVO_SIGNAL_SOURCE:
                continue
            event = _signal_to_event(row)
            if event is None or not _event_intersects_watchlist(event):
                continue
            dedup_key = f"{event['event_id']}:{','.join(_string_list(event.get('symbols')))}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            events.append(event)

        events.sort(
            key=lambda item: (
                _parse_dt(str(item.get("created_at", ""))),
                _as_float(item.get("confidence")),
                abs(_as_float(item.get("raw_score"))),
            ),
            reverse=True,
        )
        return events[:80]

    async def _recent_lane_signals(self, *, limit: int) -> list[Signal]:
        try:
            async with UnitOfWork() as uow:
                rows = await SignalsRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        return [row for row in rows if str(row.source or "") == EVO_SIGNAL_SOURCE]

    async def _pending_symbols(self, *, limit: int) -> set[str]:
        try:
            async with UnitOfWork() as uow:
                rows = await SignalsRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return set()
        return {
            str(row.symbol).upper()
            for row in rows
            if str(row.source or "") == EVO_SIGNAL_SOURCE and str(row.status.value) in {"pending", "approved"}
        }

    async def _evo_outcomes(self, *, limit: int) -> list[dict[str, object]]:
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        payloads: list[dict[str, object]] = []
        for row in rows:
            if str(row.bot_id or "") != "evo_catalyst" and str(row.source or "") != EVO_SIGNAL_SOURCE:
                continue
            payloads.append(
                {
                    "trade_id": row.trade_id,
                    "symbol": row.symbol,
                    "outcome": row.outcome.value,
                    "pnl_pct": row.pnl_pct,
                    "entry_price": row.entry_price,
                    "quantity": row.quantity,
                    "opened_at": row.opened_at.isoformat(),
                    "closed_at": None if row.closed_at is None else row.closed_at.isoformat(),
                    "market": row.market,
                }
            )
        return payloads

    async def _signal_exists(self, signal_id: str) -> bool:
        try:
            row = await SignalRecord.filter(signal_id=signal_id).first()
        except Exception:
            return False
        return row is not None

    async def _signal_count(self) -> int:
        try:
            return int(await SignalRecord.filter(source=EVO_SIGNAL_SOURCE).count())
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

    async def _persist_summary(self, summary: dict[str, object]) -> None:
        await self._append_artifact(
            event_type="lane.scan.evo_catalyst",
            payload=_json_payload(summary),
            actor=EVO_SIGNAL_SOURCE,
        )
        await self._append_outbox(
            event_type="EvoCatalystScanCompleted",
            entity_key=f"evo_catalyst:{summary.get('scan_at', _iso_now())}",
            payload=_json_payload(summary),
        )

    async def _market_snapshot(self, *, symbol: str, container: "Container | None") -> dict[str, object]:
        quote_price = 0.0
        if container is not None:
            try:
                quote = await container.broker.get_quote(symbol)
            except Exception:
                quote = {}
            if isinstance(quote, dict):
                quote_price = (
                    _as_float(quote.get("last"))
                    or _as_float(quote.get("price"))
                    or _as_float(quote.get("ask"))
                    or _as_float(quote.get("bid"))
                )
        bars = await MarketDataService().fetch_daily_bars(symbol, range_name="6mo")
        closes = [_as_float(row.get("close")) for row in bars if _as_float(row.get("close")) > 0]
        volumes = [_as_float(row.get("volume")) for row in bars if _as_float(row.get("volume")) > 0]
        last_close = closes[-1] if closes else 0.0
        reference_price = round(quote_price or last_close, 4)
        sma20 = _moving_average(closes[-20:])
        sma50 = _moving_average(closes[-50:])
        high20 = max(closes[-20:]) if len(closes) >= 20 else (max(closes) if closes else 0.0)
        volume_ratio = 0.0
        if len(volumes) >= 6:
            baseline = _moving_average(volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1])
            latest_volume = volumes[-1]
            if baseline > 0:
                volume_ratio = latest_volume / baseline
        return {
            "reference_price": reference_price,
            "momentum_20d": _pct_change(closes[-21], closes[-1]) if len(closes) >= 21 else 0.0,
            "momentum_60d": _pct_change(closes[-61], closes[-1]) if len(closes) >= 61 else 0.0,
            "sma20": sma20,
            "sma50": sma50,
            "high20": high20,
            "volume_ratio": round(volume_ratio, 4),
            "has_history": bool(closes),
        }


def _build_candidate(
    *,
    symbol: str,
    events: list[dict[str, object]],
    market_snapshot: dict[str, object],
    created_at: str,
) -> dict[str, object] | None:
    profile = EVO_WATCHLIST.get(symbol)
    if profile is None or len(events) < MIN_SUPPORT_SIGNALS:
        return None
    positive_weight = 0.0
    negative_weight = 0.0
    support_sources: set[str] = set()
    event_ids: list[str] = []
    headlines: list[str] = []
    reasons: list[str] = []
    price_sensitive_count = 0

    for event in events:
        text = f"{event.get('headline', '')} {event.get('source', '')}".lower()
        sentiment = _as_float(event.get("sentiment"))
        confidence = max(0.15, _as_float(event.get("confidence")) or 0.5)
        raw_score = _as_float(event.get("raw_score"))
        price_sensitive = bool(event.get("is_price_sensitive"))
        keyword_hits = _keyword_hits(text, values=_keywords_for_symbol(symbol))
        polarity = raw_score + (sentiment * 40.0)
        contribution = abs(polarity) * 0.14
        contribution += confidence * 14.0
        contribution += float(SOURCE_WEIGHTS.get(str(event.get("source", "")), 6))
        contribution += min(16.0, keyword_hits * 4.0)
        if price_sensitive:
            contribution += 14.0
            price_sensitive_count += 1
        if any(keyword in text for keyword in POSITIVE_KEYWORDS):
            contribution += 8.0
        if any(keyword in text for keyword in NEGATIVE_KEYWORDS):
            contribution += 10.0
            negative_weight += contribution
        elif polarity < -8.0 or sentiment < -0.18:
            negative_weight += contribution
        else:
            positive_weight += contribution
            support_sources.add(str(event.get("source", "")))
        event_ids.append(str(event.get("event_id", "")))
        headlines.append(str(event.get("headline", ""))[:160])

    catalyst_bias = positive_weight - (negative_weight * 0.75)
    if catalyst_bias < 18.0:
        return None
    score = 42.0
    score += min(24.0, catalyst_bias / 4.2)
    score += min(10.0, len(events) * 3.0)
    score += min(8.0, len(support_sources) * 2.0)
    if price_sensitive_count > 0:
        score += min(8.0, price_sensitive_count * 2.0)
        reasons.append(f"{price_sensitive_count} price-sensitive catalyst(s)")

    reference_price = _as_float(market_snapshot.get("reference_price"))
    momentum_20d = _as_float(market_snapshot.get("momentum_20d"))
    momentum_60d = _as_float(market_snapshot.get("momentum_60d"))
    sma20 = _as_float(market_snapshot.get("sma20"))
    sma50 = _as_float(market_snapshot.get("sma50"))
    high20 = _as_float(market_snapshot.get("high20"))
    volume_ratio = _as_float(market_snapshot.get("volume_ratio"))
    if reference_price > 0:
        if momentum_20d >= 1.0:
            score += 8.0
            reasons.append(f"20d momentum {momentum_20d:.1f}%")
        elif momentum_20d >= -4.0:
            score += 4.0
        elif momentum_20d <= -10.0:
            score -= 12.0
            reasons.append(f"20d drawdown {momentum_20d:.1f}%")
        if momentum_60d > 0.0:
            score += 4.0
        if reference_price > sma20 > 0:
            score += 4.0
        if sma20 > sma50 > 0:
            score += 5.0
            reasons.append("trend confirmation above 20/50d")
        if volume_ratio >= 1.2:
            score += 4.0
            reasons.append(f"volume ratio {volume_ratio:.2f}x")
        if high20 > 0 and reference_price >= high20 * 0.97:
            score += 5.0
            reasons.append("near 20d breakout range")

    score = min(95, max(0, int(round(score))))
    if score < MIN_CATALYST_SCORE:
        return None

    theme = str(profile.get("theme", "energy_transition"))
    kind = str(profile.get("kind", "equity"))
    is_etf = kind == "etf"
    stop_loss_pct = ETF_STOP_LOSS_PCT if is_etf else EQUITY_STOP_LOSS_PCT
    take_profit_pct = ETF_TAKE_PROFIT_PCT if is_etf else EQUITY_TAKE_PROFIT_PCT
    position_pct = 0.012 if score < 74 else 0.016 if score < 82 else 0.02
    confidence = min(0.95, round(0.5 + ((score - 50) / 100.0) + min(0.08, len(events) * 0.02), 4))

    return {
        "symbol": symbol,
        "theme": theme,
        "score": score,
        "confidence": confidence,
        "support_signals": len(events),
        "source": EVO_SIGNAL_SOURCE,
        "trade_type": "swing",
        "generated_at": created_at,
        "market": str(profile.get("market", _market_for_symbol(symbol))),
        "reference_price": round(reference_price, 4) if reference_price > 0 else None,
        "position_pct": position_pct,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "headline": headlines[0] if headlines else f"EVO catalyst long setup: {symbol}",
        "event_ids": event_ids[:8],
        "event_sources": sorted(source for source in support_sources if source)[:8],
        "headlines": headlines[:5],
        "reasons": reasons[:6],
        "momentum_20d": round(momentum_20d, 4),
        "momentum_60d": round(momentum_60d, 4),
        "volume_ratio": round(volume_ratio, 4),
    }


def _candidate_block_reason(
    *,
    symbol: str,
    events: list[dict[str, object]],
    market_snapshot: dict[str, object],
) -> str:
    if symbol not in EVO_WATCHLIST:
        return "not_watchlist_symbol"
    if len(events) < MIN_SUPPORT_SIGNALS:
        return "insufficient_support"
    if _as_float(market_snapshot.get("momentum_20d")) <= -10.0:
        return "trend_too_weak"
    return "below_score_threshold"


def _audit_payload_to_event(*, payload: dict[str, object], created_at: str) -> dict[str, object] | None:
    headline = str(payload.get("headline", "")).strip()
    if not headline:
        return None
    symbols = _watchlist_symbols(payload.get("symbols"))
    ticker = str(payload.get("ticker", "")).upper().strip()
    if ticker:
        symbols = _merge_symbols(symbols, [ticker])
    return {
        "event_id": str(payload.get("signal_id", "")) or _event_hash(headline=headline, source=str(payload.get("source", ""))),
        "source": str(payload.get("source", "unknown")).strip().lower(),
        "headline": headline,
        "symbols": symbols,
        "sentiment": _as_float(payload.get("sentiment")),
        "confidence": _as_float(payload.get("confidence")) or 0.5,
        "raw_score": _as_float(payload.get("raw_score")),
        "is_price_sensitive": bool(payload.get("is_price_sensitive")),
        "created_at": str(payload.get("scanned_at", created_at)),
    }


def _signal_to_event(signal: Signal) -> dict[str, object] | None:
    source = str(signal.source or "").strip().lower()
    if source not in {
        "asx_announcement",
        "ausmine",
        "alpaca_news",
        "newsapi",
        "gnews",
        "google_news",
        "evo_intel",
        "evo_quality",
    } and not source.startswith("scout_"):
        return None
    symbols = _watchlist_symbols(signal.metadata.get("symbols"))
    symbols = _merge_symbols(symbols, [signal.symbol])
    if not symbols:
        return None
    headline = str(signal.headline or "").strip()
    return {
        "event_id": signal.signal_id,
        "source": source,
        "headline": headline or f"{signal.symbol} signal",
        "symbols": symbols,
        "sentiment": _as_float(signal.metadata.get("sentiment")) or _as_float(signal.score) - 0.5,
        "confidence": _as_float(signal.confidence) or _as_float(signal.score),
        "raw_score": _as_float(signal.metadata.get("raw_score")) or (_as_float(signal.score) * 100.0),
        "is_price_sensitive": bool(signal.metadata.get("is_price_sensitive") or signal.priority >= 7),
        "created_at": signal.created_at.isoformat(),
    }


def _group_events_by_symbol(events: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for event in events:
        for symbol in _string_list(event.get("symbols")):
            grouped.setdefault(symbol, []).append(event)
    return grouped


def _event_intersects_watchlist(event: dict[str, object]) -> bool:
    return bool(_watchlist_symbols(event.get("symbols")))


def _watchlist_symbols(value: object) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    matched: list[str] = []
    for raw in values:
        symbol = str(raw or "").strip().upper()
        if not symbol:
            continue
        if symbol in EVO_WATCHLIST and symbol not in matched:
            matched.append(symbol)
            continue
        candidate = f"{symbol}.AX"
        if candidate in EVO_WATCHLIST and candidate not in matched:
            matched.append(candidate)
    return matched


def _merge_symbols(left: list[str], right: list[str]) -> list[str]:
    merged = list(left)
    for symbol in right:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            continue
        if normalized not in EVO_WATCHLIST:
            normalized = f"{normalized}.AX" if f"{normalized}.AX" in EVO_WATCHLIST else normalized
        if normalized in EVO_WATCHLIST and normalized not in merged:
            merged.append(normalized)
    return merged


def _signal_fingerprint(candidate: dict[str, object]) -> str:
    return ":".join(
        [
            str(candidate.get("symbol", "")),
            str(candidate.get("theme", "")),
            str(_as_int(candidate.get("score"))),
            "|".join(_string_list(candidate.get("event_ids"))[:4]),
        ]
    )


def _keywords_for_symbol(symbol: str) -> tuple[str, ...]:
    profile = EVO_WATCHLIST.get(symbol, {})
    aliases = tuple(str(item).lower() for item in _string_list(profile.get("aliases")))
    keywords = tuple(str(item).lower() for item in _string_list(profile.get("keywords")))
    theme = str(profile.get("theme", "")).replace("_", " ").lower()
    extras = (symbol.lower(), symbol.removesuffix(".AX").lower(), theme)
    return (*aliases, *keywords, *extras)


def _keyword_hits(text: str, *, values: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for value in values if value and value in lowered)


def _is_recent_duplicate(*, symbol: str, signals: list[Signal], now: datetime) -> bool:
    cutoff = now - timedelta(hours=RECENT_SIGNAL_LOOKBACK_HOURS)
    for signal in signals:
        if str(signal.symbol).upper() != symbol:
            continue
        if signal.created_at >= cutoff:
            return True
    return False


def _market_for_symbol(symbol: str) -> str:
    profile = EVO_WATCHLIST.get(symbol)
    if isinstance(profile, dict):
        market = str(profile.get("market", "")).strip().lower()
        if market:
            return market
    return "asx_equities" if symbol.endswith(".AX") else "us_equities"


def _event_hash(*, headline: str, source: str) -> str:
    return hashlib.md5(f"{source}:{headline}".encode("utf-8")).hexdigest()[:12]


def _moving_average(values: list[float]) -> float:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return 0.0
    return sum(cleaned) / len(cleaned)


def _pct_change(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return ((end - start) / start) * 100.0


def _increment(target: dict[str, int], key: str) -> None:
    target[key] = int(target.get(key, 0)) + 1


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


def _json_payload(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    payload: dict[str, JSONValue] = {}
    for key, item in value.items():
        payload[str(key)] = _json_value(item)
    return payload


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
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [_dict(item) for item in value if isinstance(item, dict)]


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
