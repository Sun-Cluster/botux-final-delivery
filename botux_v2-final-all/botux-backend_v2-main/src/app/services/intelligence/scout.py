from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

from app.services.intelligence.newsfeed import NewsfeedIntelService
from db.repositories._common import JSONValue
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.uow import UnitOfWork

if TYPE_CHECKING:
    from runtime.container import Container

_SCOUT_MIN_CONFIDENCE = 0.25
_SCOUT_MIN_RAW_SCORE = 12.0
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
_MACRO_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "DIA")
_CROSS_ASSET_TICKERS: tuple[str, ...] = ("GDX", "GDXJ", "COPX", "LIT", "GLD", "SLV", "XOM", "CVX")
_MACRO_KEYWORDS: tuple[str, ...] = (
    "fed",
    "fomc",
    "inflation",
    "treasury",
    "yield",
    "macro",
    "market",
    "earnings season",
    "analyst",
)
_CROSS_ASSET_KEYWORDS: tuple[str, ...] = (
    "gold",
    "silver",
    "copper",
    "lithium",
    "uranium",
    "oil",
    "gas",
    "miners",
    "metals",
    "commodity",
)
_SOURCE_LIMITS: dict[str, int] = {
    "watchlist_momentum": 4,
    "macro_regime": 3,
    "cross_asset": 3,
}


class ScoutIntelService:
    async def collect_items(
        self,
        *,
        container: "Container",
        triggered_at: str,
        scan_number: int,
    ) -> dict[str, object]:
        del container, scan_number
        items: list[dict[str, JSONValue]] = []
        per_source = {key: 0 for key in _SOURCE_LIMITS}
        skipped_reasons: dict[str, int] = {}
        fetched_source_counts: dict[str, int] = {}
        seen_keys: set[str] = set()

        articles = await self._recent_articles(limit=120)
        if not articles:
            payload = await NewsfeedIntelService().collect_articles(
                scout_theses=[],
                triggered_at=triggered_at,
            )
            articles_raw = payload.get("articles")
            counts_raw = payload.get("source_counts")
            skipped_raw = payload.get("skipped_reasons")
            if isinstance(articles_raw, list):
                articles = [row for row in articles_raw if isinstance(row, dict)]
            if isinstance(counts_raw, dict):
                fetched_source_counts = {
                    str(key): int(value)
                    for key, value in counts_raw.items()
                }
            if isinstance(skipped_raw, dict):
                skipped_reasons.update({str(key): int(value) for key, value in skipped_raw.items()})

        for article in articles:
            symbol = str(article.get("ticker", "")).upper()
            headline = str(article.get("headline", "")).strip()
            if not symbol or not headline:
                _increment(skipped_reasons, "missing_symbol")
                continue
            confidence = _as_float(article.get("confidence"))
            raw_score = _as_float(article.get("raw_score"))
            if confidence < _SCOUT_MIN_CONFIDENCE:
                _increment(skipped_reasons, "low_confidence")
                continue
            if abs(raw_score) < _SCOUT_MIN_RAW_SCORE:
                _increment(skipped_reasons, "score_below_threshold")
                continue

            collector_source = _collector_source(symbol=symbol, headline=headline)
            if per_source[collector_source] >= _SOURCE_LIMITS[collector_source]:
                _increment(skipped_reasons, "source_bucket_full")
                continue

            key = f"{collector_source}:{symbol}:{headline.lower()}"
            if key in seen_keys:
                _increment(skipped_reasons, "duplicate_article")
                continue
            seen_keys.add(key)

            upstream_source = str(article.get("source", "news"))
            if not fetched_source_counts:
                fetched_source_counts[upstream_source] = fetched_source_counts.get(upstream_source, 0) + 1
            sentiment = _as_float(article.get("sentiment"))
            item: dict[str, JSONValue] = {
                "signal_id": (
                    f"scout_item:{collector_source}:{symbol}:"
                    f"{hashlib.md5(f'{upstream_source}:{symbol}:{headline}'.encode('utf-8')).hexdigest()[:12]}"
                ),
                "source": collector_source,
                "category": _collector_category(collector_source),
                "ticker": symbol,
                "headline": headline[:200],
                "body": f"{upstream_source} article for {symbol}",
                "sentiment": round(sentiment, 4),
                "confidence": round(confidence, 4),
                "urgency": _urgency(confidence=confidence, raw_score=raw_score, price_sensitive=bool(article.get("is_price_sensitive", False))),
                "tickers": [symbol],
                "created_at": triggered_at,
                "upstream_source": upstream_source,
                "url": str(article.get("url", "")),
                "raw_score": round(raw_score, 2),
                "price_sensitive": bool(article.get("is_price_sensitive", False)),
            }
            items.append(item)
            per_source[collector_source] = per_source.get(collector_source, 0) + 1

        return {
            "items": items,
            "per_source": per_source,
            "fetched_source_counts": fetched_source_counts,
            "skipped_reasons": skipped_reasons,
        }

    async def _recent_articles(self, *, limit: int) -> list[dict[str, JSONValue]]:
        try:
            async with UnitOfWork() as uow:
                rows = await AuditLogsRepository(connection=uow.connection).list_recent_by_prefix(
                    prefix="news.article",
                    limit=limit,
                )
        except Exception:
            return []
        payloads: list[dict[str, JSONValue]] = []
        for row in rows:
            if isinstance(row.payload, dict):
                payloads.append(row.payload)
        return payloads


def _collector_source(*, symbol: str, headline: str) -> str:
    lowered = headline.lower()
    if symbol in _SCOUT_WATCHLIST:
        return "watchlist_momentum"
    if symbol in _MACRO_TICKERS or any(keyword in lowered for keyword in _MACRO_KEYWORDS):
        return "macro_regime"
    if symbol in _CROSS_ASSET_TICKERS or any(keyword in lowered for keyword in _CROSS_ASSET_KEYWORDS):
        return "cross_asset"
    if len(symbol) <= 5:
        return "watchlist_momentum"
    return "cross_asset"


def _collector_category(source: str) -> str:
    if source == "macro_regime":
        return "macro"
    if source == "cross_asset":
        return "cross_asset"
    return "news"


def _urgency(*, confidence: float, raw_score: float, price_sensitive: bool) -> str:
    if price_sensitive or confidence >= 0.8 or abs(raw_score) >= 40.0:
        return "high"
    if confidence >= 0.6 or abs(raw_score) >= 20.0:
        return "medium"
    return "low"


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1
