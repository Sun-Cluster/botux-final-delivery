from __future__ import annotations

import asyncio
from app.services.intelligence.newsfeed import NewsfeedIntelService
from app.services.scan.service import ScanService
from app.services.intelligence.scout import ScoutIntelService


# From test_newsfeed_intel_service.py


async def _run_collect_articles_uses_live_sources_case() -> None:
    async def fake_google(self, *, triggered_at: str):
        return [
            {
                "signal_id": "news.article:google_news:AAPL:1",
                "source": "google_news",
                "ticker": "AAPL",
                "headline": "Apple beats estimates on iPhone demand",
                "url": "https://example.com/apple",
                "sentiment": 0.72,
                "confidence": 0.81,
                "symbols": ["AAPL"],
                "is_price_sensitive": True,
                "raw_score": 58.32,
                "scanned_at": triggered_at,
            }
        ]

    async def fake_alpaca(self, *, triggered_at: str):
        return [
            {
                "signal_id": "news.article:alpaca_news:NVDA:1",
                "source": "alpaca_news",
                "ticker": "NVDA",
                "headline": "Nvidia raised price target after earnings beat",
                "url": "https://example.com/nvda",
                "sentiment": 0.76,
                "confidence": 0.84,
                "symbols": ["NVDA"],
                "is_price_sensitive": True,
                "raw_score": 63.84,
                "scanned_at": triggered_at,
            }
        ]

    original_network_enabled = NewsfeedIntelService._network_enabled
    original_google = NewsfeedIntelService._fetch_google_watchlist_news
    original_alpaca = NewsfeedIntelService._fetch_alpaca_watchlist_news
    NewsfeedIntelService._network_enabled = lambda self: True  # type: ignore[method-assign]
    NewsfeedIntelService._fetch_google_watchlist_news = fake_google  # type: ignore[method-assign]
    NewsfeedIntelService._fetch_alpaca_watchlist_news = fake_alpaca  # type: ignore[method-assign]
    try:
        payload = await NewsfeedIntelService().collect_articles(
            scout_theses=[
                {
                    "id": "thesis:aapl:1",
                    "ticker": "AAPL",
                    "confidence": 0.74,
                    "thesis_score": 0.68,
                    "avg_sentiment": 0.52,
                    "source": "watchlist_momentum",
                }
            ],
            triggered_at="2026-05-19T00:00:00+00:00",
        )
    finally:
        NewsfeedIntelService._network_enabled = original_network_enabled  # type: ignore[method-assign]
        NewsfeedIntelService._fetch_google_watchlist_news = original_google  # type: ignore[method-assign]
        NewsfeedIntelService._fetch_alpaca_watchlist_news = original_alpaca  # type: ignore[method-assign]

    assert len(payload["articles"]) == 3
    assert payload["source_counts"] == {
        "google_news": 1,
        "alpaca_news": 1,
        "scout_thesis_watchlist_momentum": 1,
    }


def test_collect_articles_uses_live_sources_when_available() -> None:
    asyncio.run(_run_collect_articles_uses_live_sources_case())


async def _run_news_scan_prefers_newsfeed_articles_case() -> None:
    async def fake_collect_articles(self, *, scout_theses, triggered_at):
        del scout_theses
        return {
            "articles": [
                {
                    "signal_id": "news.article:google_news:AAPL:1",
                    "source": "google_news",
                    "ticker": "AAPL",
                    "headline": "Apple beats estimates on iPhone demand",
                    "url": "https://example.com/apple",
                    "sentiment": 0.72,
                    "confidence": 0.81,
                    "symbols": ["AAPL"],
                    "is_price_sensitive": True,
                    "raw_score": 58.32,
                    "scanned_at": triggered_at,
                }
            ],
            "source_counts": {"google_news": 1},
            "skipped_reasons": {},
        }

    original_collect_articles = NewsfeedIntelService.collect_articles
    NewsfeedIntelService.collect_articles = fake_collect_articles  # type: ignore[method-assign]
    try:
        summary = await ScanService().run_news_scan(container=None, origin="test.news")  # type: ignore[arg-type]
    finally:
        NewsfeedIntelService.collect_articles = original_collect_articles  # type: ignore[method-assign]
    assert summary["articles_stored"] == 1
    assert summary["signals_generated"] == 1
    assert summary["source_counts"] == {"google_news": 1}
    assert summary["fetched_source_counts"] == {"google_news": 1}


def test_news_scan_prefers_newsfeed_articles_when_available() -> None:
    asyncio.run(_run_news_scan_prefers_newsfeed_articles_case())


# From test_scout_intel_service.py


class _Broker:
    async def get_quote(self, symbol: str) -> dict[str, float]:
        del symbol
        return {"last": 100.0, "bid": 99.9, "ask": 100.1}


class _Container:
    broker = _Broker()


async def _run_collect_items_from_news_case() -> None:
    async def fake_collect_articles(self, *, scout_theses, triggered_at):
        del self, scout_theses, triggered_at
        return {
            "articles": [
                {
                    "source": "google_news",
                    "ticker": "AAPL",
                    "headline": "Apple breaks out after analysts raise price targets",
                    "confidence": 0.82,
                    "sentiment": 0.71,
                    "raw_score": 58.22,
                    "is_price_sensitive": True,
                    "url": "https://example.com/aapl",
                },
                {
                    "source": "google_news",
                    "ticker": "SPY",
                    "headline": "SPY climbs as Fed cooling inflation narrative supports market breadth",
                    "confidence": 0.76,
                    "sentiment": 0.44,
                    "raw_score": 33.44,
                    "is_price_sensitive": True,
                    "url": "https://example.com/spy",
                },
                {
                    "source": "alpaca_news",
                    "ticker": "LIT",
                    "headline": "Lithium miners rally as supply deficit outlook improves",
                    "confidence": 0.79,
                    "sentiment": 0.67,
                    "raw_score": 41.53,
                    "is_price_sensitive": True,
                    "url": "https://example.com/lit",
                },
            ],
            "source_counts": {"google_news": 2, "alpaca_news": 1},
            "skipped_reasons": {},
        }

    original_collect_articles = NewsfeedIntelService.collect_articles
    NewsfeedIntelService.collect_articles = fake_collect_articles  # type: ignore[method-assign]
    try:
        payload = await ScoutIntelService().collect_items(
            container=_Container(),
            triggered_at="2026-05-19T00:00:00+00:00",
            scan_number=1,
        )
    finally:
        NewsfeedIntelService.collect_articles = original_collect_articles  # type: ignore[method-assign]

    assert payload["per_source"] == {
        "watchlist_momentum": 1,
        "macro_regime": 1,
        "cross_asset": 1,
    }
    assert payload["fetched_source_counts"] == {"google_news": 2, "alpaca_news": 1}
    items = payload["items"]
    assert isinstance(items, list)
    assert {str(item["source"]) for item in items} == {"watchlist_momentum", "macro_regime", "cross_asset"}


def test_scout_intel_collects_real_news_into_source_buckets() -> None:
    asyncio.run(_run_collect_items_from_news_case())


async def _run_scout_scan_prefers_news_items_case() -> None:
    async def fake_collect_items(self, *, container, triggered_at, scan_number):
        del self, container, triggered_at, scan_number
        return {
            "items": [
                {
                    "signal_id": "scout_item:watchlist_momentum:AAPL:1",
                    "source": "watchlist_momentum",
                    "category": "news",
                    "ticker": "AAPL",
                    "headline": "Apple breaks out after analysts raise price targets",
                    "body": "google_news article for AAPL",
                    "sentiment": 0.71,
                    "confidence": 0.82,
                    "urgency": "high",
                    "tickers": ["AAPL"],
                    "created_at": "2026-05-19T00:00:00+00:00",
                    "upstream_source": "google_news",
                }
            ],
            "per_source": {"watchlist_momentum": 1, "macro_regime": 0, "cross_asset": 0},
            "fetched_source_counts": {"google_news": 1},
            "skipped_reasons": {},
        }

    from app.services.intelligence.scout import ScoutIntelService as _ScoutIntelService

    original_collect_items = _ScoutIntelService.collect_items
    _ScoutIntelService.collect_items = fake_collect_items  # type: ignore[method-assign]
    try:
        summary = await ScanService().run_scout_scan(_Container(), origin="test.scout")
    finally:
        _ScoutIntelService.collect_items = original_collect_items  # type: ignore[method-assign]

    assert summary["total_items"] == 1
    assert summary["theses_count"] == 1
    assert summary["per_source"] == {"watchlist_momentum": 1, "macro_regime": 0, "cross_asset": 0}
    assert summary["fetched_source_counts"] == {"google_news": 1}


def test_scout_scan_uses_real_news_items_when_available() -> None:
    asyncio.run(_run_scout_scan_prefers_news_items_case())
