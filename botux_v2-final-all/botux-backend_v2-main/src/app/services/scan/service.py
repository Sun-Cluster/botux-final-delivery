from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from time import monotonic
from typing import TYPE_CHECKING

from loguru import logger

from app.services.lanes.ausmine import AusmineLaneService
from app.services.lanes.evo_catalyst import EvoCatalystLaneService
from app.services.intelligence.newsfeed import NewsfeedIntelService
from app.services.lanes.options import OptionsLaneService
from app.services.intelligence.scout import ScoutIntelService
from app.services.scan.utils import (
    as_float as _as_float,
    as_int as _as_int,
    canonical_lane as _canonical_lane,
    iso_now as _iso_now,
    json_payload as _json_payload,
    json_value as _json_value,
    lane_source as _lane_source,
    optional_text as _optional_text,
    parse_iso_datetime as _parse_iso_datetime,
    payload_to_object_dict as _payload_to_object_dict,
    scaled_metric as _scaled_metric,
    source_from_signal_id as _source_from_signal_id,
)
from app.services.signals.ownership import build_signal_ownership
from runtime.logging import format_log_fields
from app.services.signals.service import SignalService
from app.services.lanes.swingtrade import SwingtradeLaneService
from app.services.lanes.tradecopy import TradecopyLaneService
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.signals_repo import SignalsRepository
from db.uow import UnitOfWork
from domain.enums import LaneScanState, OrderAction
from domain.models.signal import Signal
from infra.queue.outbox_dispatcher import OutboxDispatcher

pipeline_logger = logger.bind(pipeline_module=__name__)

if TYPE_CHECKING:
    from runtime.container import Container

_SCOUT_WATCHLIST: tuple[str, ...] = (
    "AAPL",
    "NVDA",
    "MSFT",
    "TSLA",
    "AMZN",
    "GOOGL",
    "META",
    "AMD",
)

_TRADECOPY_UNIVERSE: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META")
_OPTIONS_UNIVERSE: tuple[str, ...] = ("SPY", "QQQ", "NVDA", "TSLA", "AAPL")
_SWING_UNIVERSE: tuple[str, ...] = ("META", "AMD", "JPM", "XOM", "UBER", "NFLX", "BA", "PFE")
_MINER_UNIVERSE: tuple[str, ...] = ("BHP.AX", "RIO.AX", "FMG.AX", "LIT", "GLD")

_NEWS_MIN_CONFIDENCE = 0.25
_NEWS_MIN_RAW_SCORE = 12.0

@dataclass(frozen=True)
class ScanArtifact:
    event_type: str
    payload: dict[str, JSONValue]
    actor: str


_MEMORY_AUDIT_LOGS: list[ScanArtifact] = []
_MEMORY_SIGNALS: list[dict[str, object]] = []
_MEMORY_DIRECT_DISPATCHED: set[str] = set()


class ScanService:
    @classmethod
    def clear_memory_state(cls) -> dict[str, int]:
        cleared = {
            "audit_logs": len(_MEMORY_AUDIT_LOGS),
            "signals": len(_MEMORY_SIGNALS),
            "direct_dispatched": len(_MEMORY_DIRECT_DISPATCHED),
        }
        _MEMORY_AUDIT_LOGS.clear()
        _MEMORY_SIGNALS.clear()
        _MEMORY_DIRECT_DISPATCHED.clear()
        return cleared

    async def run_scout_scan(self, container: Container, *, origin: str = "api.scout_scan") -> dict[str, object]:
        started = monotonic()
        scan_number = await self._next_scan_number("scout.scan")
        created_at = _iso_now()
        scout_payload = await ScoutIntelService().collect_items(
            container=container,
            triggered_at=created_at,
            scan_number=scan_number,
        )
        items_raw = scout_payload.get("items")
        per_source_raw = scout_payload.get("per_source")
        skipped_reasons_raw = scout_payload.get("skipped_reasons")
        fetched_source_counts_raw = scout_payload.get("fetched_source_counts")
        items: list[dict[str, JSONValue]] = (
            list(items_raw)
            if isinstance(items_raw, list)
            else []
        )
        per_source: dict[str, int] = (
            {str(key): int(value) for key, value in per_source_raw.items()}
            if isinstance(per_source_raw, dict)
            else {"watchlist_momentum": 0, "macro_regime": 0, "cross_asset": 0}
        )
        skipped_reasons: dict[str, int] = (
            {str(key): int(value) for key, value in skipped_reasons_raw.items()}
            if isinstance(skipped_reasons_raw, dict)
            else {}
        )
        fetched_source_counts: dict[str, int] = (
            {str(key): int(value) for key, value in fetched_source_counts_raw.items()}
            if isinstance(fetched_source_counts_raw, dict)
            else {}
        )

        if not items:
            items = []
            per_source = {"watchlist_momentum": 0, "macro_regime": 0, "cross_asset": 0}
            for index, symbol in enumerate(_SCOUT_WATCHLIST[:6]):
                quote = await container.broker.get_quote(symbol)
                sentiment = _scaled_metric(symbol, "sentiment", -0.9, 0.9)
                confidence = _scaled_metric(symbol, "confidence", 0.55, 0.92)
                source = ("watchlist_momentum", "macro_regime", "cross_asset")[index % 3]
                item: dict[str, JSONValue] = {
                    "signal_id": f"scout_item:{source}:{symbol}:{scan_number}:{index}",
                    "source": source,
                    "category": "market_intel",
                    "ticker": symbol,
                    "headline": f"{symbol} scout read from {source.replace('_', ' ')}",
                    "body": (
                        f"{symbol} quote {_as_float(quote.get('last')):.2f} with "
                        f"sentiment {sentiment:+.2f} and confidence {confidence:.2f}"
                    ),
                    "sentiment": round(sentiment, 4),
                    "confidence": round(confidence, 4),
                    "urgency": "high" if confidence >= 0.8 else "medium",
                    "tickers": [symbol],
                    "created_at": created_at,
                }
                per_source[source] = per_source.get(source, 0) + 1
                items.append(item)
            skipped_reasons["fallback_synthetic"] = skipped_reasons.get("fallback_synthetic", 0) + 1

        theses = self._build_scout_theses(items=items, created_at=created_at)
        for item in items:
            await self._append_artifact(event_type="scout.item", payload=item, actor="scout_engine")
        for thesis in theses:
            await self._append_artifact(event_type="scout.thesis", payload=thesis, actor="scout_engine")
        summary_payload_raw: dict[str, object] = {
            "scan_number": scan_number,
            "total_items": len(items),
            "stored": len(items),
            "theses_count": len(theses),
            "theses_stored": len(theses),
            "theses": theses[:5],
            "per_source": per_source,
            "fetched_source_counts": fetched_source_counts,
            "skipped_reasons": skipped_reasons,
            "elapsed_seconds": round(monotonic() - started, 2),
            "timestamp": created_at,
        }
        summary_payload = _json_payload(summary_payload_raw)
        await self._append_artifact(event_type="scout.scan", payload=summary_payload, actor="scout_engine")
        await self._append_outbox_safe(
            event_type="ScoutScanCompleted",
            entity_key=f"scout:{scan_number}",
            payload=summary_payload,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "scan.completed", format_log_fields({"origin": origin, "scan": "scout", "scan_number": scan_number, "total_items": len(items), "theses_count": len(theses), "elapsed_seconds": round(monotonic() - started, 2)}))
        return summary_payload_raw

    async def get_scout_status(self) -> dict[str, object]:
        scan_count = await self._count_audit_events(prefix="scout.scan")
        total_items = await self._count_audit_events(prefix="scout.item")
        last_scan = await self._latest_payload("scout.scan")
        per_source: dict[str, int] = {}
        if last_scan is not None:
            raw = last_scan.get("per_source")
            if isinstance(raw, dict):
                per_source = {str(key): int(value) for key, value in raw.items() if isinstance(value, int)}
        collectors = [
            {"name": "watchlist_momentum", "enabled": True, "last_count": per_source.get("watchlist_momentum", 0)},
            {"name": "macro_regime", "enabled": True, "last_count": per_source.get("macro_regime", 0)},
            {"name": "cross_asset", "enabled": True, "last_count": per_source.get("cross_asset", 0)},
        ]
        return {
            "engine": "scout",
            "version": "1.0",
            "scan_count": scan_count,
            "total_items_collected": total_items,
            "last_scan": None if last_scan is None else last_scan.get("timestamp"),
            "collectors": collectors,
            "enabled_count": len(collectors),
            "disabled_count": 0,
        }

    async def list_scout_items(self, *, source: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        payloads = await self._list_payloads(prefix="scout.item", limit=max(limit * 3, 50))
        rows: list[dict[str, object]] = []
        for payload in payloads:
            row = _payload_to_object_dict(payload)
            if source is not None and source.lower() != str(row.get("source", "")).lower():
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    async def list_scout_theses(self, *, limit: int = 25) -> list[dict[str, object]]:
        payloads = await self._list_payloads(prefix="scout.thesis", limit=limit)
        return [_payload_to_object_dict(payload) for payload in payloads[:limit]]

    async def bridge_scout_to_signals(
        self,
        *,
        origin: str = "api.scout_bridge",
        container: Container | None = None,
    ) -> dict[str, object]:
        theses = await self.list_scout_theses(limit=25)
        bridged = 0
        skipped = 0
        sources: dict[str, int] = {}
        errors: list[str] = []
        for thesis in theses:
            source = str(thesis.get("source", "scout"))
            confidence = _as_float(thesis.get("confidence"))
            score = _as_float(thesis.get("thesis_score"))
            symbol = str(thesis.get("ticker", "")).upper()
            direction = str(thesis.get("direction", "buy")).lower()
            if not symbol or confidence < 0.55 or score < 0.58:
                skipped += 1
                continue
            signal_id = f"scout_{source}_{symbol}_{str(thesis.get('id', 'scan')).replace(':', '_')}"
            existing = await self._signal_exists(signal_id)
            if existing:
                skipped += 1
                continue
            action = OrderAction.BUY if direction != "sell" else OrderAction.SELL
            try:
                ownership = build_signal_ownership(
                    source=f"scout_{source}",
                    symbol=symbol,
                    lane_hint="scout",
                    strategy_hint=source,
                )
                persisted = await self._persist_signal(
                    signal=Signal(
                        signal_id=signal_id,
                        symbol=symbol,
                        action=action,
                        score=min(max(score, 0.0), 1.0),
                        confidence=min(max(confidence, 0.0), 1.0),
                        source=f"scout_{source}",
                        lane_hint="scout",
                        strategy_hint=source,
                        headline=str(thesis.get("summary", f"{symbol} scout thesis"))[:200],
                        scan_timestamp=_parse_iso_datetime(thesis.get("created_at")),
                        metadata={
                            "direction": direction,
                            "thesis_id": str(thesis.get("id", "")),
                            "raw_confidence": confidence,
                            "raw_score": score,
                            **ownership,
                        },
                    ),
                )
                if persisted:
                    bridged += 1
                    event_source = f"scout_{source}"
                    sources[event_source] = sources.get(event_source, 0) + 1
                else:
                    errors.append(f"persist_failed:{signal_id}"[:120])
            except Exception as exc:
                errors.append(str(exc)[:120])
        bridge_payload: dict[str, JSONValue] = {
            "bridged": bridged,
            "skipped": skipped,
            "errors": [str(item) for item in errors],
            "sources": {str(key): value for key, value in sources.items()},
            "timestamp": _iso_now(),
        }
        await self._append_artifact(event_type="scout.bridge", payload=bridge_payload, actor="scout_bridge")
        await self._append_outbox_safe(
            event_type="ScoutBridgeCompleted",
            entity_key=_iso_now(),
            payload=_json_payload({"bridged": bridged, "skipped": skipped, "sources": sources}),
        )
        dispatch_stats = await self._dispatch_signal_outbox(container=container, signal_count=bridged, origin=origin)
        pipeline_logger.log("INFO", "pipeline.{} {}", "scan.bridge.completed", format_log_fields({"origin": origin, "bridge": "scout_to_signals", "bridged": bridged, "skipped": skipped, "errors": len(errors), "sources": sources}))
        return {
            "bridged": bridged,
            "skipped": skipped,
            "errors": list(errors),
            "sources": dict(sources),
            "dispatch": dispatch_stats,
        }

    async def run_news_scan(self, container: Container, *, origin: str = "api.news_scan") -> dict[str, object]:
        triggered_at = _iso_now()
        scout_theses = await self.list_scout_theses(limit=8)
        intel_payload = await NewsfeedIntelService().collect_articles(
            scout_theses=scout_theses,
            triggered_at=triggered_at,
        )
        articles_raw = intel_payload.get("articles")
        source_counts_raw = intel_payload.get("source_counts")
        skipped_reasons_raw = intel_payload.get("skipped_reasons")
        skipped_reasons: dict[str, int] = (
            {str(key): int(value) for key, value in skipped_reasons_raw.items()}
            if isinstance(skipped_reasons_raw, dict)
            else {}
        )
        articles = (
            list(articles_raw)
            if isinstance(articles_raw, list)
            else []
        )
        if not articles:
            articles = self._build_news_articles(scout_theses=scout_theses, triggered_at=triggered_at, container=container)
        signals_generated = 0
        fetched_source_counts: dict[str, int] = (
            {str(key): int(value) for key, value in source_counts_raw.items()}
            if isinstance(source_counts_raw, dict)
            else {}
        )
        source_counts: dict[str, int] = {}
        for article in articles:
            await self._append_artifact(event_type="news.article", payload=article, actor="newsfeed_intel")
            source = str(article.get("source", "news"))
            source_counts[source] = source_counts.get(source, 0) + 1
            confidence = _as_float(article.get("confidence"))
            raw_score = _as_float(article.get("raw_score"))
            symbol = str(article.get("ticker", "")).upper()
            if not symbol:
                skipped_reasons["missing_symbol"] = skipped_reasons.get("missing_symbol", 0) + 1
                continue
            if confidence < _NEWS_MIN_CONFIDENCE:
                skipped_reasons["low_confidence"] = skipped_reasons.get("low_confidence", 0) + 1
                continue
            if abs(raw_score) < _NEWS_MIN_RAW_SCORE:
                skipped_reasons["score_below_threshold"] = skipped_reasons.get("score_below_threshold", 0) + 1
                continue
            if symbol:
                sentiment = _as_float(article.get("sentiment"))
                action = OrderAction.BUY if sentiment >= 0.0 else OrderAction.SELL
                signal_id = f"news_{source}_{symbol}_{hashlib.md5(str(article['headline']).encode('utf-8')).hexdigest()[:12]}"
                existing = await self._signal_exists(signal_id)
                if not existing:
                    ownership = build_signal_ownership(
                        source=source,
                        symbol=symbol,
                        lane_hint="news",
                        strategy_hint="newsfeed_intel",
                    )
                    persisted = await self._persist_signal(
                        signal=Signal(
                            signal_id=signal_id,
                            symbol=symbol,
                            action=action,
                            score=min(max(raw_score / 100.0, 0.0), 1.0),
                            confidence=min(max(confidence, 0.0), 1.0),
                            source=source,
                            lane_hint="news",
                            strategy_hint="newsfeed_intel",
                            headline=str(article.get("headline", f"{symbol} market brief"))[:200],
                            scan_timestamp=_parse_iso_datetime(article.get("scanned_at")),
                            metadata={
                                "raw_score": raw_score,
                                "sentiment": sentiment,
                                "url": str(article.get("url", "")),
                                "price_sensitive": bool(article.get("is_price_sensitive", False)),
                                **ownership,
                            },
                        ),
                    )
                    if persisted:
                        signals_generated += 1
                else:
                    skipped_reasons["duplicate_signal"] = skipped_reasons.get("duplicate_signal", 0) + 1
        summary_payload_raw: dict[str, object] = {
            "status": "news scan triggered",
            "status_code": "triggered",
            "scan_state": LaneScanState.COMPLETED.value,
            "triggered_at": triggered_at,
            "articles_stored": len(articles),
            "signals_generated": signals_generated,
            "source_counts": source_counts,
            "fetched_source_counts": fetched_source_counts,
            "skipped_reasons": skipped_reasons,
        }
        summary_payload = _json_payload(summary_payload_raw)
        await self._append_artifact(event_type="news.scan", payload=summary_payload, actor="newsfeed_intel")
        await self._append_outbox_safe(
            event_type="NewsScanCompleted",
            entity_key=triggered_at,
            payload=summary_payload,
        )
        dispatch_stats = await self._dispatch_signal_outbox(
            container=container,
            signal_count=signals_generated,
            origin=origin,
        )
        summary_payload_raw["dispatch"] = dispatch_stats
        pipeline_logger.log("INFO", "pipeline.{} {}", "scan.completed", format_log_fields({"origin": origin, "scan": "news", "triggered_at": triggered_at, "articles_stored": len(articles), "signals_generated": signals_generated, "source_counts": source_counts}))
        return summary_payload_raw

    async def list_news_articles(self, *, limit: int = 30) -> list[dict[str, object]]:
        payloads = await self._list_payloads(prefix="news.article", limit=limit)
        return [_payload_to_object_dict(payload) for payload in payloads[:limit]]

    async def list_recent_signals(self, *, limit: int = 100) -> list[dict[str, object]]:
        return await self._recent_signal_payloads(limit=limit)

    async def run_lane_scan(
        self,
        *,
        lane: str,
        container: Container,
        origin: str | None = None,
    ) -> dict[str, object]:
        canonical = _canonical_lane(lane)
        effective_origin = origin or f"api.lane_scan.{canonical}"
        if canonical == "tradecopy":
            summary = await TradecopyLaneService().run_scan(container=container)
        elif canonical == "options":
            summary = await OptionsLaneService().run_scan(container=container)
        elif canonical == "swingtrade":
            summary = await SwingtradeLaneService().run_scan(container=container)
        elif canonical == "evo_catalyst":
            summary = await EvoCatalystLaneService().run_scan(container=container)
        else:
            summary = await AusmineLaneService().run_scan(container=container)
        summary["lane"] = lane
        candidate_rows = summary.get("candidates")
        candidate_count = len(candidate_rows) if isinstance(candidate_rows, list) else None
        pipeline_logger.log("INFO", "pipeline.{} {}", "scan.completed", format_log_fields({"origin": effective_origin, "scan": canonical, "signals": summary.get("signals"), "candidates": candidate_count}))
        created_signals = _as_int(summary.get("signals"))
        summary["dispatch"] = await self._dispatch_signal_outbox(
            container=container,
            signal_count=created_signals,
            origin=effective_origin,
        )
        return summary

    async def get_lane_scan(self, *, lane: str) -> dict[str, object]:
        canonical = _canonical_lane(lane)
        latest = await self._latest_payload(f"lane.scan.{canonical}")
        if latest is None:
            return {
                "lane": lane,
                "scan_status": "idle",
                "scan_state": LaneScanState.IDLE.value,
                "scan_status_code": "idle",
                "candidates": [],
                "count": 0,
                "generated_at": _iso_now(),
            }
        candidates_raw = latest.get("candidates")
        candidates = candidates_raw if isinstance(candidates_raw, list) else []
        return {
            "lane": lane,
            "scan_status": str(latest.get("status", "ok")),
            "scan_state": str(latest.get("scan_state", LaneScanState.COMPLETED.value)),
            "scan_status_code": str(latest.get("status_code", "ok")),
            "candidates": [_payload_to_object_dict(item) for item in candidates if isinstance(item, dict)],
            "count": len(candidates),
            "generated_at": str(latest.get("scan_at", _iso_now())),
        }

    async def get_lane_status(self, *, lane: str, bot_id: str, enabled: bool, lifecycle_state: str) -> dict[str, object]:
        canonical = _canonical_lane(lane)
        latest = await self._latest_payload(f"lane.scan.{canonical}")
        count = await self._count_signals_for_source(_lane_source(canonical))
        last_scan = None if latest is None else latest.get("scan_at")
        status = "active" if enabled else "idle"
        if canonical == "tradecopy":
            return await TradecopyLaneService().get_status(
                bot_id=bot_id,
                enabled=enabled,
                lifecycle_state=lifecycle_state,
            )
        if canonical == "options":
            return await OptionsLaneService().get_status(
                bot_id=bot_id,
                enabled=enabled,
                lifecycle_state=lifecycle_state,
            )
        if canonical == "swingtrade":
            return await SwingtradeLaneService().get_status(
                bot_id=bot_id,
                enabled=enabled,
                lifecycle_state=lifecycle_state,
            )
        if canonical == "evo_catalyst":
            return await EvoCatalystLaneService().get_status(
                bot_id=bot_id,
                enabled=enabled,
                lifecycle_state=lifecycle_state,
            )
        return await AusmineLaneService().get_status(
            bot_id=bot_id,
            enabled=enabled,
            lifecycle_state=lifecycle_state,
        )

    async def _persist_lane_signals(
        self,
        *,
        source: str,
        candidates: list[dict[str, JSONValue]],
        action: OrderAction,
        headline_prefix: str,
    ) -> int:
        created = 0
        for candidate in candidates:
            symbol = str(candidate.get("symbol", candidate.get("underlying", ""))).upper()
            if not symbol:
                continue
            score_100 = _as_float(candidate.get("score"))
            signal_score = min(max(score_100 / 100.0, 0.0), 1.0) if score_100 > 1.0 else min(max(score_100, 0.0), 1.0)
            signal_id = f"{source}:{symbol}:{hashlib.md5(f'{source}:{symbol}:{candidate}'.encode('utf-8')).hexdigest()[:12]}"
            existing = await self._signal_exists(signal_id)
            if existing:
                continue
            persisted = await self._persist_signal(
                signal=Signal(
                    signal_id=signal_id,
                    symbol=symbol,
                    action=action,
                    score=signal_score if signal_score > 0.0 else 0.65,
                    confidence=min(max(_as_float(candidate.get("confidence", signal_score or 0.65)), 0.0), 1.0),
                    source=source,
                    lane_hint=source,
                    strategy_hint=str(candidate.get("strategy", source)).lower(),
                    headline=f"{headline_prefix}: {symbol}"[:200],
                    scan_timestamp=_parse_iso_datetime(candidate.get("scanned_at")),
                    metadata={
                        "candidate": candidate,
                        "raw_score": score_100,
                    },
                ),
            )
            if not persisted:
                continue
            await self._append_artifact(
                event_type=f"lane.candidate.{source}",
                payload={
                    "signal_id": signal_id,
                    "headline": f"{headline_prefix}: {symbol}",
                    "candidate": candidate,
                    "created_at": _iso_now(),
                },
                actor=source,
            )
            created += 1
        return created

    async def _persist_lane_summary(self, *, canonical_lane: str, payload: dict[str, JSONValue]) -> None:
        await self._append_artifact(event_type=f"lane.scan.{canonical_lane}", payload=payload, actor=canonical_lane)
        await self._append_outbox_safe(
            event_type=f"{canonical_lane.title()}ScanCompleted",
            entity_key=f"{canonical_lane}:{_iso_now()}",
            payload=payload,
        )

    async def _signal_exists(self, signal_id: str) -> bool:
        if any(str(row.get("signal_id", "")) == signal_id for row in _MEMORY_SIGNALS):
            return True
        try:
            async with UnitOfWork() as uow:
                repo = SignalsRepository(connection=uow.connection)
                return (await repo.get_by_signal_id(signal_id)) is not None
        except Exception:
            return False

    async def _list_payloads(self, *, prefix: str, limit: int) -> list[dict[str, JSONValue]]:
        try:
            async with UnitOfWork() as uow:
                repo = AuditLogsRepository(connection=uow.connection)
                rows = await repo.list_recent_by_prefix(prefix=prefix, limit=limit)
            payloads: list[dict[str, JSONValue]] = []
            for row in rows:
                if isinstance(row.payload, dict):
                    payloads.append(row.payload)
            if payloads:
                return payloads
        except Exception:
            pass
        return [artifact.payload for artifact in _MEMORY_AUDIT_LOGS if artifact.event_type.startswith(prefix)][:limit]

    async def _latest_payload(self, event_type: str) -> dict[str, JSONValue] | None:
        try:
            async with UnitOfWork() as uow:
                repo = AuditLogsRepository(connection=uow.connection)
                row = await repo.latest_by_type(event_type=event_type)
            if row is not None and isinstance(row.payload, dict):
                return row.payload
        except Exception:
            pass
        for artifact in _MEMORY_AUDIT_LOGS:
            if artifact.event_type == event_type:
                return artifact.payload
        return None

    async def _count_audit_events(self, *, prefix: str) -> int:
        payloads = await self._list_payloads(prefix=prefix, limit=1000)
        return len(payloads)

    async def _next_scan_number(self, event_type: str) -> int:
        latest = await self._latest_payload(event_type)
        if latest is None:
            return 1
        previous = latest.get("scan_number")
        if isinstance(previous, int):
            return previous + 1
        return 1

    async def _count_signals_for_source(self, source: str) -> int:
        rows = await self._recent_signal_payloads(limit=500)
        return sum(1 for row in rows if str(row.get("source", "")).lower() == source)

    async def _recent_signal_payloads(self, *, limit: int) -> list[dict[str, object]]:
        try:
            async with UnitOfWork() as uow:
                repo = SignalsRepository(connection=uow.connection)
                signals = await repo.list_recent(limit=limit)
            rows: list[dict[str, object]] = []
            for signal in signals:
                rows.append(
                    {
                        "signal_id": signal.signal_id,
                        "ticker": signal.symbol,
                        "symbol": signal.symbol,
                        "headline": signal.headline,
                        "action": signal.action.value,
                        "status": signal.status.value,
                        "score": signal.score,
                        "conf": signal.confidence,
                        "confidence": signal.confidence,
                        "source": signal.source,
                        "lane_hint": signal.lane_hint,
                        "strategy_hint": signal.strategy_hint,
                        "schema_version": signal.schema_version,
                        "created_at": signal.created_at.isoformat(),
                    }
                )
            if rows:
                return rows
        except Exception:
            logger.exception("scan service recent signal query failed")
        return []

    def _build_scout_theses(
        self,
        *,
        items: list[dict[str, JSONValue]],
        created_at: str,
    ) -> list[dict[str, JSONValue]]:
        theses: list[dict[str, JSONValue]] = []
        for index, item in enumerate(items[:4]):
            sentiment = _as_float(item.get("sentiment"))
            confidence = _as_float(item.get("confidence"))
            ticker = str(item.get("ticker", "")).upper()
            direction = "buy" if sentiment >= 0.0 else "sell"
            theses.append(
                {
                    "id": f"thesis:{ticker}:{index}:{created_at}",
                    "ticker": ticker,
                    "market": "US",
                    "direction": direction,
                    "thesis_score": round(min(max((abs(sentiment) * 0.45) + (confidence * 0.55), 0.0), 0.99), 4),
                    "confidence": round(confidence, 4),
                    "support_count": 1,
                    "source_diversity": 1,
                    "urgency": item.get("urgency", "medium"),
                    "evidence": [str(item.get("headline", ""))],
                    "avg_sentiment": round(sentiment, 4),
                    "source": str(item.get("source", "scout")),
                    "timestamp": created_at,
                    "summary": f"{ticker} {direction.upper()} thesis from {item.get('source', 'scout')}",
                }
            )
        return theses

    def _build_news_articles(
        self,
        *,
        scout_theses: list[dict[str, object]],
        triggered_at: str,
        container: Container,
    ) -> list[dict[str, JSONValue]]:
        articles: list[dict[str, JSONValue]] = []
        for index, thesis in enumerate(scout_theses[:4]):
            ticker = str(thesis.get("ticker", "AAPL")).upper()
            source = f"scout_thesis_{str(thesis.get('source', 'scout')).lower()}"
            sentiment = _as_float(thesis.get("avg_sentiment"))
            raw_score = round(_as_float(thesis.get("thesis_score")) * 100.0, 2)
            articles.append(
                {
                    "signal_id": f"news.article:{source}:{ticker}:{index}",
                    "source": source,
                    "ticker": ticker,
                    "headline": f"{ticker} thesis promoted to market brief",
                    "url": "",
                    "sentiment": round(sentiment, 4),
                    "confidence": round(_as_float(thesis.get("confidence")), 4),
                    "symbols": [ticker],
                    "is_price_sensitive": raw_score >= 70.0,
                    "raw_score": raw_score,
                    "scanned_at": triggered_at,
                }
            )
        if not articles:
            for index, ticker in enumerate(_SCOUT_WATCHLIST[:4]):
                articles.append(
                    {
                        "signal_id": f"news.article:alpaca_news:{ticker}:{index}",
                        "source": "alpaca_news",
                        "ticker": ticker,
                        "headline": f"{ticker} watchlist momentum check",
                        "url": "",
                        "sentiment": round(_scaled_metric(ticker, "news_sent", -0.6, 0.8), 4),
                        "confidence": round(_scaled_metric(ticker, "news_conf", 0.6, 0.9), 4),
                        "symbols": [ticker],
                        "is_price_sensitive": True,
                        "raw_score": round(_scaled_metric(ticker, "news_score", 58.0, 88.0), 2),
                        "scanned_at": triggered_at,
                    }
                )
        return articles

    async def _append_artifact(
        self,
        *,
        event_type: str,
        payload: dict[str, JSONValue],
        actor: str,
    ) -> None:
        _MEMORY_AUDIT_LOGS.insert(0, ScanArtifact(event_type=event_type, payload=payload, actor=actor))
        try:
            async with UnitOfWork() as uow:
                await AuditLogsRepository(connection=uow.connection).append(
                    event_type=event_type,
                    payload=payload,
                    actor=actor,
                )
        except Exception:
            return

    async def _append_outbox_safe(
        self,
        *,
        event_type: str,
        entity_key: str,
        payload: dict[str, JSONValue],
    ) -> None:
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

    async def _persist_signal(self, *, signal: Signal) -> bool:
        _MEMORY_SIGNALS.insert(
            0,
            {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "action": signal.action.value,
                "status": signal.status.value,
                "score": signal.score,
                "confidence": signal.confidence,
                "priority": signal.priority,
                "source": signal.source,
                "lane_hint": signal.lane_hint,
                "strategy_hint": signal.strategy_hint,
                "dedup_key": signal.dedup_key,
                "blocked_reason": signal.blocked_reason,
                "created_at": signal.created_at.isoformat(),
                "scan_timestamp": signal.scan_timestamp.isoformat() if signal.scan_timestamp is not None else None,
            },
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.created", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "action": signal.action.value, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint, "score": signal.score, "confidence": signal.confidence, "status": signal.status.value, "scan_timestamp": signal.scan_timestamp}))
        try:
            await SignalService().ingest_signal(signal)
        except Exception:
            logger.exception("signal persistence failed signal_id={}", signal.signal_id)
            pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.persist_failed", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint}))
            return True
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.persisted", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint}))
        return True

    async def _dispatch_signal_outbox(
        self,
        *,
        container: Container | None,
        signal_count: int,
        origin: str,
    ) -> dict[str, int]:
        if signal_count <= 0:
            return {"checked": 0, "dispatched": 0, "processed": 0, "failed": 0, "skipped": 0}
        if container is None or container.process_manager is None:
            return {"checked": 0, "dispatched": 0, "processed": 0, "failed": 0, "skipped": 0}
        try:
            stats = await OutboxDispatcher(container.queue_bus).dispatch_pending(limit=max(signal_count * 3, 25))
        except Exception:
            stats = await self._dispatch_memory_signals(container=container, limit=signal_count)
        pipeline_logger.log("INFO", "pipeline.{} {}", "scan.dispatch_completed", format_log_fields({"origin": origin, "signal_count": signal_count, "checked": stats.get("checked", 0), "dispatched": stats.get("dispatched", 0), "failed": stats.get("failed", 0), "skipped": stats.get("skipped", 0)}))
        return stats

    async def _dispatch_memory_signals(self, *, container: Container, limit: int) -> dict[str, int]:
        process_manager = container.process_manager
        if process_manager is None:
            return {"checked": 0, "dispatched": 0, "processed": 0, "failed": 0, "skipped": 0}
        pending_rows = [
            row
            for row in _MEMORY_SIGNALS
            if str(row.get("status", "pending")).lower() == "pending"
            and str(row.get("signal_id", "")) not in _MEMORY_DIRECT_DISPATCHED
        ][: max(limit, 0)]
        dispatched = 0
        failed = 0
        for row in pending_rows:
            try:
                signal = Signal(
                    signal_id=str(row.get("signal_id", "")),
                    symbol=str(row.get("symbol", "")),
                    action=str(row.get("action", "buy")),
                    status=str(row.get("status", "pending")),
                    score=_as_float(row.get("score")),
                    confidence=_as_float(row.get("confidence")),
                    priority=_as_int(row.get("priority")) or 5,
                    source=str(row.get("source", "unknown")),
                    lane_hint=_optional_text(row.get("lane_hint")),
                    strategy_hint=_optional_text(row.get("strategy_hint")),
                    dedup_key=_optional_text(row.get("dedup_key")),
                    blocked_reason=_optional_text(row.get("blocked_reason")),
                    created_at=_parse_iso_datetime(row.get("created_at")) or datetime.now(timezone.utc),
                    scan_timestamp=_parse_iso_datetime(row.get("scan_timestamp")),
                )
                await process_manager.publish_signal(signal)
                _MEMORY_DIRECT_DISPATCHED.add(signal.signal_id)
                dispatched += 1
            except Exception:
                failed += 1
        return {
            "checked": len(pending_rows),
            "dispatched": dispatched,
            "processed": dispatched,
            "failed": failed,
            "skipped": 0,
        }
