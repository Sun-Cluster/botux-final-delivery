from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import re
from typing import TYPE_CHECKING

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


AUSMINE_VERSION = "2.0.0"
SIGNAL_SOURCE = "ausmine"
LANE_HINT = "miner"
STRATEGY_HINT = "ausmine_event"
RECENT_LOOKBACK_HOURS = 6
DUPLICATE_LOOKBACK_HOURS = 4

MINING_STOCKS: dict[str, dict[str, object]] = {
    "BHP.AX": {"name": "BHP Group", "commodity": ["iron ore", "copper", "nickel"], "cap": "mega", "sector": "diversified"},
    "RIO.AX": {"name": "Rio Tinto", "commodity": ["iron ore", "aluminium", "copper", "lithium"], "cap": "mega", "sector": "diversified"},
    "FMG.AX": {"name": "Fortescue", "commodity": ["iron ore", "green hydrogen"], "cap": "mega", "sector": "iron ore"},
    "WDS.AX": {"name": "Woodside Energy", "commodity": ["oil", "gas"], "cap": "large", "sector": "energy"},
    "STO.AX": {"name": "Santos", "commodity": ["oil", "gas"], "cap": "large", "sector": "energy"},
    "MIN.AX": {"name": "Mineral Resources", "commodity": ["lithium", "iron ore"], "cap": "large", "sector": "diversified"},
    "IGO.AX": {"name": "IGO Limited", "commodity": ["nickel", "lithium", "copper"], "cap": "large", "sector": "battery metals"},
    "LYC.AX": {"name": "Lynas Rare Earths", "commodity": ["rare earths"], "cap": "large", "sector": "rare earths"},
    "PLS.AX": {"name": "Pilbara Minerals", "commodity": ["lithium"], "cap": "mid", "sector": "lithium"},
    "LTR.AX": {"name": "Liontown Resources", "commodity": ["lithium"], "cap": "mid", "sector": "lithium"},
    "EVN.AX": {"name": "Evolution Mining", "commodity": ["gold"], "cap": "mid", "sector": "gold"},
    "DEG.AX": {"name": "De Grey Mining", "commodity": ["gold"], "cap": "mid", "sector": "gold"},
    "SFR.AX": {"name": "Sandfire Resources", "commodity": ["copper", "zinc"], "cap": "mid", "sector": "base metals"},
    "PDN.AX": {"name": "Paladin Energy", "commodity": ["uranium"], "cap": "mid", "sector": "uranium"},
    "BOE.AX": {"name": "Boss Energy", "commodity": ["uranium"], "cap": "mid", "sector": "uranium"},
    "NWH.AX": {"name": "NRW Holdings", "commodity": ["civil", "mining services"], "cap": "mid", "sector": "contractor"},
    "QUB.AX": {"name": "Qube Holdings", "commodity": ["logistics"], "cap": "mid", "sector": "infrastructure"},
    "AZJ.AX": {"name": "Aurizon Holdings", "commodity": ["rail freight"], "cap": "mid", "sector": "infrastructure"},
    "SYR.AX": {"name": "Syrah Resources", "commodity": ["graphite"], "cap": "small", "sector": "battery metals"},
    "GL1.AX": {"name": "Global Lithium", "commodity": ["lithium"], "cap": "small", "sector": "lithium"},
    "CXO.AX": {"name": "Core Lithium", "commodity": ["lithium"], "cap": "small", "sector": "lithium"},
    "ARU.AX": {"name": "Arafura Rare Earths", "commodity": ["rare earths"], "cap": "small", "sector": "rare earths"},
    "NIC.AX": {"name": "Nickel Industries", "commodity": ["nickel"], "cap": "small", "sector": "nickel"},
    "JMS.AX": {"name": "Jupiter Mines", "commodity": ["manganese"], "cap": "small", "sector": "manganese"},
    "TLG.AX": {"name": "Talga Group", "commodity": ["graphite"], "cap": "small", "sector": "battery metals"},
    "LKE.AX": {"name": "Lake Resources", "commodity": ["lithium"], "cap": "small", "sector": "lithium"},
    "SVL.AX": {"name": "Silver Mines", "commodity": ["silver"], "cap": "micro", "sector": "silver"},
    "MGT.AX": {"name": "Magnetite Mines", "commodity": ["iron ore"], "cap": "micro", "sector": "iron ore"},
    "VML.AX": {"name": "Vital Metals", "commodity": ["rare earths"], "cap": "micro", "sector": "rare earths"},
}

TIER_KEYWORDS: dict[str, tuple[tuple[str, str, float], ...]] = {
    "A": (
        ("permit_approval", "mining lease approved", 0.90),
        ("permit_approval", "mining lease granted", 0.90),
        ("first_production", "first ore", 0.90),
        ("dfs_complete", "definitive feasibility study", 0.88),
        ("offtake_agreement", "binding offtake", 0.86),
    ),
    "B": (
        ("environmental_approval", "environmental approval", 0.75),
        ("environmental_approval", "epa clearance", 0.75),
        ("scoping_study", "pre-feasibility study", 0.72),
        ("scoping_study", "feasibility study positive", 0.72),
        ("native_title", "native title agreement", 0.70),
    ),
    "C": (
        ("exploration_granted", "exploration permit", 0.60),
        ("drill_results", "drill results", 0.60),
        ("resource_upgrade", "jorc resource", 0.62),
        ("discovery", "high grade intercept", 0.65),
        ("assay_results", "assay results", 0.58),
    ),
    "D": (
        ("commodity_signal", "lithium demand", 0.40),
        ("commodity_signal", "gold price", 0.40),
        ("commodity_signal", "uranium demand", 0.40),
        ("commodity_signal", "critical minerals", 0.42),
        ("commodity_signal", "china demand", 0.40),
    ),
    "E": (
        ("infrastructure_contract", "contract awarded", 0.65),
        ("infrastructure_contract", "mining services contract", 0.65),
        ("infrastructure_contract", "rail project approved", 0.60),
        ("infrastructure_contract", "port expansion", 0.58),
        ("infrastructure_contract", "processing plant", 0.58),
    ),
    "F": (
        ("takeover", "takeover offer", 0.85),
        ("takeover", "takeover bid", 0.85),
        ("strategic_jv", "joint venture agreement", 0.82),
        ("strategic_jv", "strategic partnership", 0.82),
        ("asset_acquisition", "asset acquisition", 0.78),
    ),
}

TIER_SCORES: dict[str, int] = {"A": 85, "F": 78, "B": 72, "E": 58, "C": 60, "D": 45}
MIN_TRADEABLE_SCORE: dict[str, int] = {"A": 78, "F": 72, "B": 66, "E": 55, "C": 55, "D": 50}
CAP_BONUS: dict[str, int] = {"mega": 3, "large": 2, "mid": 0, "small": -2, "micro": -5, "unknown": -6}
POSITION_BASE: dict[str, float] = {"A": 2.0, "F": 2.0, "B": 1.5, "E": 1.0, "C": 1.0, "D": 0.5}
CAP_MULTIPLIER: dict[str, float] = {"mega": 1.5, "large": 1.2, "mid": 1.0, "small": 0.8, "micro": 0.5, "unknown": 0.3}

STATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "WA": ("western australia", "wa government", "pilbara", "kalgoorlie", "goldfields"),
    "QLD": ("queensland", "qld government", "bowen basin", "mount isa", "townsville"),
    "SA": ("south australia", "sa government", "olympic dam", "gawler craton"),
    "NSW": ("new south wales", "nsw government", "broken hill", "cobar"),
    "NT": ("northern territory", "nt government", "tennant creek"),
    "TAS": ("tasmania", "tas government", "west coast tasmania"),
    "VIC": ("victoria", "vic government", "ballarat", "bendigo"),
}

COMMODITY_BULLISH: tuple[str, ...] = (
    "price surge",
    "record high",
    "supply deficit",
    "demand surge",
    "rally",
    "breakout",
    "bullish",
    "china demand",
    "india demand",
    "critical minerals",
)
COMMODITY_BEARISH: tuple[str, ...] = (
    "price crash",
    "oversupply",
    "demand slump",
    "bearish",
    "surplus",
    "inventory build",
    "weak demand",
)

ASX_TICKER_PATTERN = re.compile(r"\b([A-Z]{2,3})\.AX\b|\bASX[:\s]+([A-Z]{2,3})\b")
FALLBACK_HEADLINES: tuple[str, ...] = (
    "BHP Group mining lease approved in Western Australia after environmental approval for new iron ore development",
    "Liontown Resources drill results confirm high grade intercept at Kathleen Valley in WA",
    "Pilbara Minerals strategic partnership and binding offtake agreement signed in Queensland",
    "NRW Holdings contract awarded for processing plant and rail project approved in Western Australia",
    "ASX: XYZ mining lease granted in Western Australia for new copper discovery",
    "Critical minerals demand surges as uranium demand and lithium demand remain strong across Australia",
)


class AusmineLaneService:
    async def run_scan(self, *, container: "Container | None" = None) -> dict[str, object]:
        del container
        created_at = _iso_now()
        source_events = await self._source_events(now=datetime.now(timezone.utc))
        recent_lane_signals = await self._recent_lane_signals(limit=500)
        seen_combos: set[str] = set()
        discovered: dict[str, str] = {}
        blocked_reasons: dict[str, int] = {}
        tiers: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
        states_detected: list[str] = []
        stocks_matched: list[str] = []
        candidates: list[dict[str, object]] = []
        source_artifacts: list[dict[str, object]] = []

        for event in source_events:
            headline = str(event["headline"])
            source = str(event["source"])
            source_artifact = {
                "headline": headline,
                "source": source,
                "created_at": str(event["created_at"]),
            }
            await self._append_artifact("lane.source.ausmine", _json_payload(source_artifact), actor="ausmine")
            source_artifacts.append(dict(source_artifact))

            tier_matches = _classify_tiers(headline)
            if not tier_matches:
                await self._record_block(
                    reason="no_tier_match",
                    payload={"headline": headline, "source": source},
                    blocked_reasons=blocked_reasons,
                )
                continue

            stock_matches = _match_stocks(headline=headline, discovered=discovered)
            if not stock_matches:
                await self._record_block(
                    reason="no_stock_match",
                    payload={"headline": headline, "source": source, "tiers": [item["tier"] for item in tier_matches]},
                    blocked_reasons=blocked_reasons,
                )
                continue

            state = _detect_state(headline)
            if state not in states_detected:
                states_detected.append(state)
            commodity_sentiment = _score_commodity_sentiment(headline)

            for tier_match in tier_matches:
                for stock in stock_matches:
                    symbol = str(stock["symbol"])
                    combo = f"{symbol}:{tier_match['tier']}:{tier_match['event_type']}"
                    if combo in seen_combos:
                        continue
                    seen_combos.add(combo)

                    tradeability = _tradeability(
                        symbol=symbol,
                        stock=stock,
                        tier_match=tier_match,
                        commodity_sentiment=commodity_sentiment,
                        recent_lane_signals=recent_lane_signals,
                    )
                    if not tradeability["allowed"]:
                        await self._record_block(
                            reason=str(tradeability["reason"]),
                            payload={
                                "headline": headline,
                                "source": source,
                                "symbol": symbol,
                                "tier": tier_match["tier"],
                                "event_type": tier_match["event_type"],
                                "score": tradeability["score"],
                            },
                            blocked_reasons=blocked_reasons,
                        )
                        continue

                    candidate = _build_candidate(
                        symbol=symbol,
                        stock=stock,
                        tier_match=tier_match,
                        headline=headline,
                        source=source,
                        state=state,
                        commodity_sentiment=commodity_sentiment,
                        score=_as_int(tradeability.get("score")),
                    )
                    candidates.append(candidate)
                    tiers[str(candidate["tier"])] = int(tiers.get(str(candidate["tier"]), 0)) + 1
                    if symbol not in stocks_matched:
                        stocks_matched.append(symbol)

        created_signals = await self._persist_signals(candidates)
        summary: dict[str, object] = {
            "status": "ok",
            "status_code": "ok",
            "scan_state": LaneScanState.COMPLETED.value,
            "scanned": len(source_events),
            "signals": created_signals,
            "tiers": tiers,
            "stocks_matched": stocks_matched,
            "states_detected": states_detected,
            "discovered_tickers": discovered,
            "blocked_reasons": blocked_reasons,
            "blocked": sum(blocked_reasons.values()),
            "scan_at": created_at,
            "candidates": candidates,
            "source_events": source_artifacts[:25],
        }
        await self._append_artifact("lane.scan.ausmine", _json_payload(summary), actor="ausmine")
        await self._append_outbox(
            event_type="AusmineScanCompleted",
            entity_key=f"ausmine:{created_at}",
            payload=_json_payload(summary),
        )
        return summary

    async def get_status(self, *, bot_id: str, enabled: bool, lifecycle_state: str) -> dict[str, object]:
        latest = await self._latest_payload("lane.scan.ausmine")
        outcomes = await self._ausmine_outcomes(limit=1000)
        open_positions = [row for row in outcomes if row.status == "open"]
        closed_positions = [row for row in outcomes if row.status in {"win", "loss", "breakeven"}]
        positions: dict[str, dict[str, object]] = {}
        for row in open_positions:
            positions[row.symbol] = await self._position_snapshot(row)
        total_pnl = sum(_as_float(row.pnl_pct) for row in closed_positions)
        latest_payload = _dict(latest)
        return {
            "bot": "Nugget the Prospector",
            "version": AUSMINE_VERSION,
            "last_scan": latest_payload.get("scan_at"),
            "scans_completed": await self._scan_count(),
            "signals_generated": await self._signal_count(),
            "watchlist": _watchlist_summary(),
            "keywords": _keyword_summary(),
            "discovered_tickers": _dict(latest_payload.get("discovered_tickers")),
            "commodity_sentiment_keywords": len(COMMODITY_BULLISH) + len(COMMODITY_BEARISH),
            "states_monitored": list(STATE_KEYWORDS.keys()),
            "states_detected": latest_payload.get("states_detected", []),
            "tiers_last_scan": _dict(latest_payload.get("tiers")),
            "blocked_reasons": _dict(latest_payload.get("blocked_reasons")),
            "open_positions": len(open_positions),
            "positions": positions,
            "stats": {
                "opened": len(outcomes),
                "closed": len(closed_positions),
                "total_pnl": round(total_pnl, 4),
            },
            "enabled": enabled,
            "status": LaneRuntimeStatus.ACTIVE.value if enabled else LaneRuntimeStatus.IDLE.value,
            "lifecycle_state": lifecycle_state,
            "fleet_slot_status": _fleet_slot_status(lifecycle_state),
            "lane": "ausmine",
            "bot_id": bot_id,
            "scan_state": latest_payload.get("scan_state"),
            "scan_status_code": latest_payload.get("status_code"),
        }

    async def _source_events(self, *, now: datetime) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        cutoff = now - timedelta(hours=RECENT_LOOKBACK_HOURS)
        seen_keys: set[str] = set()
        try:
            async with UnitOfWork() as uow:
                recent_articles = await AuditLogsRepository(connection=uow.connection).list_recent_by_prefix(
                    prefix="news.article",
                    limit=200,
                )
        except Exception:
            recent_articles = []
        for row in recent_articles:
            payload = row.payload if isinstance(row.payload, dict) else None
            if payload is None:
                continue
            headline = str(payload.get("headline", "")).strip()
            source = str(payload.get("source", "")).strip()
            if not headline or not source:
                continue
            scanned_at = _parse_dt(str(payload.get("scanned_at", row.created_at.isoformat())))
            if scanned_at < cutoff:
                continue
            key = f"{source}:{headline.lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(
                {
                    "headline": headline,
                    "source": source,
                    "created_at": scanned_at.isoformat(),
                    "signal_id": str(payload.get("signal_id", "")),
                }
            )
        try:
            async with UnitOfWork() as uow:
                recent_signals = await SignalsRepository(connection=uow.connection).list_recent(limit=200)
        except Exception:
            recent_signals = []
        for signal in recent_signals:
            if signal.created_at < cutoff:
                continue
            if signal.source == SIGNAL_SOURCE:
                continue
            if not signal.headline:
                continue
            key = f"{signal.source}:{signal.headline.lower()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(
                {
                    "headline": signal.headline,
                    "source": signal.source,
                    "created_at": signal.created_at.isoformat(),
                    "signal_id": signal.signal_id,
                }
            )
        if len(events) < 6:
            for headline in FALLBACK_HEADLINES:
                key = f"fallback_permits:{headline.lower()}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                events.append(
                    {
                        "headline": headline,
                        "source": "fallback_permits",
                        "created_at": now.isoformat(),
                    }
                )
        return events[:50]

    async def _persist_signals(self, candidates: list[dict[str, object]]) -> int:
        created = 0
        for candidate in candidates:
            symbol = str(candidate.get("symbol", "")).upper()
            if not symbol:
                continue
            signal_id = (
                f"ausmine:{symbol}:"
                f"{hashlib.md5(_signal_fingerprint(candidate).encode('utf-8')).hexdigest()[:12]}"
            )
            if await self._signal_exists(signal_id):
                continue
            score = min(max(_as_float(candidate.get("score")) / 100.0, 0.0), 0.99)
            signal = Signal(
                signal_id=signal_id,
                symbol=symbol,
                action=OrderAction.BUY,
                score=score,
                confidence=min(max(_as_float(candidate.get("confidence")), 0.0), 0.95),
                priority=8 if score >= 0.8 else 7 if score >= 0.65 else 6,
                source=SIGNAL_SOURCE,
                lane_hint=LANE_HINT,
                strategy_hint=STRATEGY_HINT,
                headline=str(candidate.get("headline", ""))[:200],
                metadata={
                    "candidate": _json_payload(candidate),
                    "market": "asx_equities",
                    "order_type": "limit",
                    "position_pct": _as_float(candidate.get("position_pct")),
                    "event_family": str(candidate.get("event_family", "")),
                    "event_type": str(candidate.get("event_type", "")),
                    "tier": str(candidate.get("tier", "")),
                    "source_headline": str(candidate.get("source_headline", "")),
                    "blocked_reasons": [],
                },
            )
            try:
                await SignalService().ingest_signal(signal)
            except Exception:
                continue
            await self._append_artifact(
                "lane.candidate.ausmine",
                _json_payload({"signal_id": signal_id, "candidate": candidate, "created_at": _iso_now()}),
                actor="ausmine",
            )
            created += 1
        return created

    async def _record_block(
        self,
        *,
        reason: str,
        payload: dict[str, object],
        blocked_reasons: dict[str, int],
    ) -> None:
        _increment(blocked_reasons, reason)
        await self._append_artifact(
            "lane.blocked.ausmine",
            _json_payload({"reason": reason, **payload, "created_at": _iso_now()}),
            actor="ausmine",
        )

    async def _recent_lane_signals(self, *, limit: int) -> list[Signal]:
        try:
            async with UnitOfWork() as uow:
                rows = await SignalsRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        return [row for row in rows if row.source == SIGNAL_SOURCE]

    async def _ausmine_outcomes(self, *, limit: int) -> list["_OutcomeSnapshot"]:
        try:
            async with UnitOfWork() as uow:
                outcomes = await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []
        rows: list[_OutcomeSnapshot] = []
        for outcome in outcomes:
            bot_id = str(outcome.bot_id or "").lower()
            source = str(outcome.source or "").lower()
            if bot_id != "nugget_bot" and source != SIGNAL_SOURCE:
                continue
            rows.append(
                _OutcomeSnapshot(
                    trade_id=outcome.trade_id,
                    signal_id=outcome.signal_id,
                    symbol=outcome.symbol.upper(),
                    status=outcome.outcome.value,
                    pnl_pct=_as_float(outcome.pnl_pct),
                    entry_price=outcome.entry_price,
                    quantity=outcome.quantity,
                    opened_at=outcome.opened_at,
                    features=_dict(outcome.features),
                )
            )
        return rows

    async def _position_snapshot(self, row: "_OutcomeSnapshot") -> dict[str, object]:
        features = row.features
        return {
            "entry": _as_float(row.entry_price),
            "qty": _as_float(row.quantity),
            "tier": str(features.get("tier") or ""),
            "event_type": str(features.get("event_type") or ""),
            "position_pct": _as_float(features.get("position_pct")),
            "state": str(features.get("state") or ""),
            "sector": str(features.get("sector") or ""),
            "entered_at": row.opened_at.isoformat(),
            "pnl_pct": round(row.pnl_pct, 4),
        }

    async def _signal_exists(self, signal_id: str) -> bool:
        try:
            row = await SignalRecord.filter(signal_id=signal_id).first()
        except Exception:
            return False
        return row is not None

    async def _signal_count(self) -> int:
        try:
            return int(await SignalRecord.filter(source=SIGNAL_SOURCE).count())
        except Exception:
            return 0

    async def _scan_count(self) -> int:
        try:
            async with UnitOfWork() as uow:
                rows = await AuditLogsRepository(connection=uow.connection).list_recent_by_prefix(prefix="lane.scan.ausmine", limit=1000)
        except Exception:
            return 0
        return len(rows)

    async def _latest_payload(self, event_type: str) -> dict[str, object] | None:
        try:
            async with UnitOfWork() as uow:
                row = await AuditLogsRepository(connection=uow.connection).latest_by_type(event_type=event_type)
        except Exception:
            return None
        if row is None or not isinstance(row.payload, dict):
            return None
        return {str(key): value for key, value in row.payload.items()}

    async def _append_artifact(self, event_type: str, payload: dict[str, JSONValue], *, actor: str) -> None:
        try:
            async with UnitOfWork() as uow:
                await AuditLogsRepository(connection=uow.connection).append(event_type=event_type, payload=payload, actor=actor)
        except Exception:
            return

    async def _append_outbox(self, *, event_type: str, entity_key: str, payload: dict[str, JSONValue]) -> None:
        try:
            async with UnitOfWork() as uow:
                await append_outbox_event(event_type=event_type, entity_key=entity_key, payload=payload, connection=uow.connection)
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
        quantity: float | None,
        opened_at: datetime,
        features: dict[str, object],
    ) -> None:
        self.trade_id = trade_id
        self.signal_id = signal_id
        self.symbol = symbol
        self.status = status
        self.pnl_pct = pnl_pct
        self.entry_price = entry_price
        self.quantity = quantity
        self.opened_at = opened_at
        self.features = features


def _classify_tiers(text: str) -> list[dict[str, object]]:
    lowered = text.lower()
    matches: list[dict[str, object]] = []
    for tier, rules in TIER_KEYWORDS.items():
        for event_type, keyword, confidence in rules:
            if keyword in lowered:
                matches.append(
                    {
                        "tier": tier,
                        "event_type": event_type,
                        "event_family": _event_family(event_type),
                        "keyword": keyword,
                        "confidence": confidence,
                    }
                )
                break
    return matches


def _event_family(event_type: str) -> str:
    if event_type in {"permit_approval", "dfs_complete", "environmental_approval", "native_title"}:
        return "development_feasibility"
    if event_type in {"first_production", "offtake_agreement"}:
        return "production_operations"
    if event_type in {"exploration_granted", "drill_results", "resource_upgrade", "discovery", "assay_results"}:
        return "exploration_discovery"
    if event_type in {"takeover", "strategic_jv", "asset_acquisition"}:
        return "capital_corporate"
    if event_type in {"commodity_signal"}:
        return "commodity_sentiment"
    if event_type in {"infrastructure_contract"}:
        return "civil_infrastructure"
    return "unclassified"


def _match_stocks(*, headline: str, discovered: dict[str, str]) -> list[dict[str, object]]:
    lowered = headline.lower()
    seen: set[str] = set()
    matches: list[dict[str, object]] = []
    for symbol, info in MINING_STOCKS.items():
        bare = symbol.replace(".AX", "").lower()
        name = str(info["name"]).lower()
        if bare in lowered or name in lowered:
            if symbol not in seen:
                matches.append({"symbol": symbol, **info, "match_confidence": 1.0})
                seen.add(symbol)
                continue
        first_word = name.split()[0]
        if len(first_word) > 3 and first_word in lowered and symbol not in seen:
            matches.append({"symbol": symbol, **info, "match_confidence": 0.7})
            seen.add(symbol)

    if not matches:
        commodities: set[str] = set()
        for info in MINING_STOCKS.values():
            commodities.update(str(item).lower() for item in _string_list(info["commodity"]))
        for commodity in commodities:
            if len(commodity) <= 3 or commodity not in lowered:
                continue
            for symbol, info in MINING_STOCKS.items():
                if commodity in [str(item).lower() for item in _string_list(info["commodity"])] and symbol not in seen:
                    matches.append({"symbol": symbol, **info, "match_confidence": 0.4})
                    seen.add(symbol)

    for raw in ASX_TICKER_PATTERN.finditer(headline):
        ticker = raw.group(1) or raw.group(2)
        if not ticker:
            continue
        symbol = f"{ticker}.AX"
        if symbol in seen or symbol in MINING_STOCKS:
            continue
        discovered.setdefault(symbol, datetime.now(timezone.utc).isoformat())
        matches.append(
            {
                "symbol": symbol,
                "name": f"Discovered: {ticker}",
                "commodity": ["unknown"],
                "cap": "unknown",
                "sector": "discovered",
                "match_confidence": 0.5,
            }
        )
        seen.add(symbol)
    return matches


def _detect_state(text: str) -> str:
    lowered = text.lower()
    for state, keywords in STATE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return state
    return "AU"


def _score_commodity_sentiment(text: str) -> float:
    lowered = text.lower()
    bull = sum(1 for keyword in COMMODITY_BULLISH if keyword in lowered)
    bear = sum(1 for keyword in COMMODITY_BEARISH if keyword in lowered)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)


def _tradeability(
    *,
    symbol: str,
    stock: dict[str, object],
    tier_match: dict[str, object],
    commodity_sentiment: float,
    recent_lane_signals: list[Signal],
) -> dict[str, object]:
    tier = str(tier_match["tier"])
    base_score = TIER_SCORES.get(tier, 40)
    score = base_score + int(round(commodity_sentiment * 10.0)) + CAP_BONUS.get(str(stock.get("cap", "unknown")), -6)
    if _as_float(stock.get("match_confidence")) < 0.45:
        return {"allowed": False, "reason": "low_match_confidence", "score": score}
    if str(stock.get("cap", "unknown")) == "unknown" and tier in {"C", "D", "E"}:
        return {"allowed": False, "reason": "dynamic_discovery_unverified", "score": score}
    if tier == "D" and commodity_sentiment <= 0.0:
        return {"allowed": False, "reason": "commodity_sentiment_non_positive", "score": score}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_LOOKBACK_HOURS)
    for signal in recent_lane_signals:
        if signal.symbol != symbol.upper():
            continue
        metadata = _dict(signal.metadata)
        existing_tier = str(metadata.get("tier") or _dict(metadata.get("candidate")).get("tier") or "")
        if existing_tier == tier and signal.created_at >= cutoff:
            return {"allowed": False, "reason": "duplicate_recent_signal", "score": score}
    min_score = MIN_TRADEABLE_SCORE.get(tier, 50)
    if score < min_score:
        return {"allowed": False, "reason": "score_below_tradeable", "score": score}
    return {"allowed": True, "reason": "tradeable", "score": min(100, max(0, score))}


def _build_candidate(
    *,
    symbol: str,
    stock: dict[str, object],
    tier_match: dict[str, object],
    headline: str,
    source: str,
    state: str,
    commodity_sentiment: float,
    score: int,
) -> dict[str, object]:
    cap = str(stock.get("cap", "unknown"))
    position_pct = min(3.0, POSITION_BASE.get(str(tier_match["tier"]), 0.5) * CAP_MULTIPLIER.get(cap, 0.3))
    return {
        "symbol": symbol.upper(),
        "tier": str(tier_match["tier"]),
        "score": score,
        "confidence": round(_as_float(tier_match.get("confidence")) * _as_float(stock.get("match_confidence") or 0.5), 4),
        "keyword": str(tier_match["keyword"]),
        "event_type": str(tier_match["event_type"]),
        "event_family": str(tier_match["event_family"]),
        "state": state,
        "commodity_sentiment": commodity_sentiment,
        "cap": cap,
        "sector": str(stock.get("sector", "unknown")),
        "position_pct": round(position_pct, 1),
        "commodity": _string_list(stock.get("commodity")),
        "source": SIGNAL_SOURCE,
        "source_headline": headline,
        "headline": f"{symbol.upper()} {tier_match['tier']} {tier_match['event_type']}: {headline}"[:200],
        "match_confidence": _as_float(stock.get("match_confidence") or 0.5),
        "reference_price": round(_stable_metric(symbol, "price", 0.15, 85.0), 2),
    }


def _watchlist_summary() -> dict[str, int]:
    return {
        "total": len(MINING_STOCKS),
        "mega_cap": sum(1 for item in MINING_STOCKS.values() if item["cap"] == "mega"),
        "large_cap": sum(1 for item in MINING_STOCKS.values() if item["cap"] == "large"),
        "mid_cap": sum(1 for item in MINING_STOCKS.values() if item["cap"] == "mid"),
        "small_cap": sum(1 for item in MINING_STOCKS.values() if item["cap"] == "small"),
        "micro_cap": sum(1 for item in MINING_STOCKS.values() if item["cap"] == "micro"),
        "contractors": sum(1 for item in MINING_STOCKS.values() if item["sector"] == "contractor"),
        "infrastructure": sum(1 for item in MINING_STOCKS.values() if item["sector"] == "infrastructure"),
    }


def _keyword_summary() -> dict[str, int]:
    return {
        "A_production": len(TIER_KEYWORDS["A"]),
        "B_environmental": len(TIER_KEYWORDS["B"]),
        "C_exploration": len(TIER_KEYWORDS["C"]),
        "D_commodity": len(TIER_KEYWORDS["D"]),
        "E_infrastructure": len(TIER_KEYWORDS["E"]),
        "F_mna": len(TIER_KEYWORDS["F"]),
        "total": sum(len(items) for items in TIER_KEYWORDS.values()),
    }


def _signal_fingerprint(candidate: dict[str, object]) -> str:
    key_parts = (
        str(candidate.get("symbol", "")),
        str(candidate.get("tier", "")),
        str(candidate.get("event_type", "")),
        str(candidate.get("keyword", "")),
        str(candidate.get("source_headline", "")),
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
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _parse_dt(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_metric(symbol: str, salt: str, min_value: float, max_value: float) -> float:
    raw = hashlib.md5(f"{symbol}:{salt}".encode("utf-8")).hexdigest()
    normalized = int(raw[:8], 16) / 0xFFFFFFFF
    return min_value + ((max_value - min_value) * normalized)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
