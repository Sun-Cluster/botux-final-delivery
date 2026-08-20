from __future__ import annotations

import asyncio
import hashlib
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, cast

import httpx

from app.services.runtime_config.service import RuntimeConfigService

from db.repositories._common import JSONValue

_WATCHLIST_US: tuple[str, ...] = (
    "AAPL",
    "NVDA",
    "MSFT",
    "TSLA",
    "AMZN",
    "GOOGL",
    "META",
    "AMD",
    "INTC",
    "JPM",
    "BAC",
    "XOM",
    "CVX",
    "GS",
    "V",
    "MA",
    "UNH",
    "JNJ",
    "MU",
    "NFLX",
    "DIS",
    "BA",
    "PFE",
    "SPY",
    "QQQ",
)
_WATCHLIST_ASX: tuple[str, ...] = (
    "BHP.AX",
    "RIO.AX",
    "PLS.AX",
    "LTR.AX",
    "NCM.AX",
    "NST.AX",
    "IGO.AX",
    "SFR.AX",
)
_WATCHLIST_ALL: tuple[str, ...] = (*_WATCHLIST_US, *_WATCHLIST_ASX)

_COMPANY_NAMES: dict[str, str] = {
    "apple": "AAPL",
    "nvidia": "NVDA",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "amd": "AMD",
    "intel": "INTC",
    "jpmorgan": "JPM",
    "bank of america": "BAC",
    "exxon": "XOM",
    "chevron": "CVX",
    "goldman": "GS",
    "visa": "V",
    "mastercard": "MA",
    "unitedhealth": "UNH",
    "johnson & johnson": "JNJ",
    "micron": "MU",
    "netflix": "NFLX",
    "disney": "DIS",
    "boeing": "BA",
    "pfizer": "PFE",
    "bhp": "BHP.AX",
    "rio tinto": "RIO.AX",
    "pilbara": "PLS.AX",
    "liontown": "LTR.AX",
    "newcrest": "NCM.AX",
    "northern star": "NST.AX",
    "igo": "IGO.AX",
    "sandfire": "SFR.AX",
}

_POSITIVE_WORDS: dict[str, float] = {
    "beat": 2.0,
    "exceeded": 2.0,
    "outperform": 1.8,
    "raised guidance": 2.5,
    "earnings beat": 2.5,
    "revenue growth": 2.0,
    "strong quarter": 2.0,
    "raised price target": 2.5,
    "overweight": 1.8,
    "top pick": 2.0,
    "all-time high": 2.0,
    "breakout": 2.0,
    "momentum": 1.5,
    "acquisition": 1.4,
    "approval": 2.0,
    "buyback": 1.8,
    "dividend": 1.5,
    "upgrade": 2.0,
    "surge": 2.0,
    "rally": 1.6,
}

_NEGATIVE_WORDS: dict[str, float] = {
    "missed": -2.0,
    "downgrade": -2.5,
    "cut guidance": -2.5,
    "warning": -1.8,
    "layoffs": -2.0,
    "underweight": -1.8,
    "sell rating": -2.5,
    "price target cut": -2.5,
    "recall": -2.0,
    "lawsuit": -2.0,
    "sec": -1.8,
    "subpoena": -2.5,
    "plunge": -2.5,
    "collapse": -3.0,
    "fraud": -3.0,
    "investigation": -2.0,
    "capital raise": -1.8,
    "dilution": -2.0,
}

_PRICE_SENSITIVE_TERMS: tuple[str, ...] = (
    "takeover",
    "merger",
    "acquisition",
    "capital raise",
    "trading halt",
    "suspension",
    "substantial holder",
    "director",
    "earnings",
    "guidance",
    "fda",
    "regulatory",
    "dividend",
    "buyback",
    "stock split",
    "analyst",
    "upgrade",
    "downgrade",
)


class NewsfeedIntelService:
    def __init__(self) -> None:
        self._alpaca_key = ""
        self._alpaca_secret = ""
        self._alpaca_data_url = "https://data.alpaca.markets"
        self._newsapi_key = ""
        self._disable_live_fetch = False

    async def collect_articles(
        self,
        *,
        scout_theses: list[dict[str, object]],
        triggered_at: str,
    ) -> dict[str, object]:
        await self._load_settings()
        source_counts: dict[str, int] = {}
        skipped_reasons: dict[str, int] = {}
        articles: list[dict[str, JSONValue]] = []

        fetched_groups: list[list[dict[str, JSONValue]]] = []
        if self._network_enabled():
            if not self._alpaca_key or not self._alpaca_secret:
                _increment(skipped_reasons, "alpaca_credentials_missing")
            if not self._newsapi_key:
                _increment(skipped_reasons, "newsapi_key_missing")
            results = await asyncio.gather(
                self._fetch_google_watchlist_news(triggered_at=triggered_at),
                self._fetch_alpaca_general_news(triggered_at=triggered_at),
                self._fetch_alpaca_watchlist_news(triggered_at=triggered_at),
                self._fetch_newsapi_watchlist_news(triggered_at=triggered_at),
                self._fetch_asx_announcements(triggered_at=triggered_at),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    _increment(skipped_reasons, "fetch_error")
                    continue
                fetched_groups.append(cast(list[dict[str, JSONValue]], result))
        else:
            _increment(skipped_reasons, "network_fetch_disabled")

        for batch in fetched_groups:
            for article in batch:
                source = str(article.get("source", "unknown"))
                source_counts[source] = source_counts.get(source, 0) + 1
                articles.append(article)

        scout_articles = self._build_scout_articles(
            scout_theses=scout_theses,
            triggered_at=triggered_at,
            skipped_reasons=skipped_reasons,
        )
        for article in scout_articles:
            source = str(article.get("source", "unknown"))
            source_counts[source] = source_counts.get(source, 0) + 1
            articles.append(article)

        deduped = _dedupe_articles(articles)
        deduped.sort(
            key=lambda row: (
                1 if str(row.get("ticker", "")).strip() else 0,
                abs(_as_float(row.get("raw_score"))),
                _as_float(row.get("confidence")),
            ),
            reverse=True,
        )
        return {
            "articles": deduped[:60],
            "source_counts": source_counts,
            "skipped_reasons": skipped_reasons,
        }

    async def _load_settings(self) -> None:
        runtime = RuntimeConfigService()
        self._alpaca_key = str((await runtime.resolve("broker.alpaca.api_key")).value or "").strip()
        self._alpaca_secret = str((await runtime.resolve("broker.alpaca.secret_key")).value or "").strip()
        self._alpaca_data_url = str((await runtime.resolve("broker.alpaca.data_url")).value or self._alpaca_data_url).strip()
        self._newsapi_key = str((await runtime.resolve("intel.news_api_key")).value or "").strip()
        self._disable_live_fetch = bool((await runtime.resolve_bool("intel.disable_live_fetch")).value)

    async def _fetch_google_watchlist_news(self, *, triggered_at: str) -> list[dict[str, JSONValue]]:
        queries = (
            "AAPL OR NVDA OR MSFT OR TSLA stocks",
            "AMZN OR GOOGL OR META OR AMD stocks",
            "SPY OR QQQ market earnings analysts",
        )
        articles: list[dict[str, JSONValue]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
            for query in queries:
                url = (
                    "https://news.google.com/rss/search?q="
                    f"{urllib.parse.quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
                )
                response = await client.get(url)
                response.raise_for_status()
                root = ET.fromstring(response.text)
                for item in root.findall(".//item")[:12]:
                    headline = _clean_google_title(item.findtext("title", ""))
                    link = item.findtext("link", "")
                    article = self._build_article(
                        source="google_news",
                        headline=headline,
                        url=link,
                        body="",
                        symbols=[],
                        triggered_at=triggered_at,
                    )
                    if article is not None:
                        articles.append(article)
        return articles

    async def _fetch_alpaca_general_news(self, *, triggered_at: str) -> list[dict[str, JSONValue]]:
        if not self._alpaca_key or not self._alpaca_secret:
            return []

        headers = {
            "APCA-API-KEY-ID": self._alpaca_key,
            "APCA-API-SECRET-KEY": self._alpaca_secret,
        }
        articles: list[dict[str, JSONValue]] = []
        url = f"{self._alpaca_data_url}/v1beta1/news?limit=40&sort=desc"
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0, headers=headers) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            payload = response.json()
            items = payload.get("news", payload) if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                return []
            for item in items[:40]:
                article = self._build_article(
                    source="alpaca_news",
                    headline=str(item.get("headline", "")),
                    url=str(item.get("url", "")),
                    body=str(item.get("summary", "")),
                    symbols=_symbols_list(item.get("symbols")),
                    triggered_at=triggered_at,
                )
                if article is not None:
                    articles.append(article)
        return articles

    async def _fetch_alpaca_watchlist_news(self, *, triggered_at: str) -> list[dict[str, JSONValue]]:
        if not self._alpaca_key or not self._alpaca_secret:
            return []

        headers = {
            "APCA-API-KEY-ID": self._alpaca_key,
            "APCA-API-SECRET-KEY": self._alpaca_secret,
        }
        articles: list[dict[str, JSONValue]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0, headers=headers) as client:
            for start in range(0, min(len(_WATCHLIST_US), 20), 10):
                symbols = list(_WATCHLIST_US[start : start + 10])
                url = (
                    f"{self._alpaca_data_url}/v1beta1/news?"
                    f"symbols={','.join(symbols)}&limit=20&sort=desc"
                )
                response = await client.get(url)
                if response.status_code != 200:
                    continue
                payload = response.json()
                items = payload.get("news", payload) if isinstance(payload, dict) else payload
                if not isinstance(items, list):
                    continue
                for item in items[:20]:
                    article = self._build_article(
                        source="alpaca_news",
                        headline=str(item.get("headline", "")),
                        url=str(item.get("url", "")),
                        body=str(item.get("summary", "")),
                        symbols=_symbols_list(item.get("symbols")),
                        triggered_at=triggered_at,
                    )
                    if article is not None:
                        articles.append(article)
        return articles

    async def _fetch_newsapi_watchlist_news(self, *, triggered_at: str) -> list[dict[str, JSONValue]]:
        if not self._newsapi_key:
            return []
        queries = (
            "AAPL OR NVDA OR MSFT OR TSLA OR AMZN stocks",
            "GOOGL OR META OR AMD OR INTC earnings guidance",
            "BHP OR RIO OR PLS OR LTR ASX mining",
        )
        articles: list[dict[str, JSONValue]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
            for query in queries:
                url = "https://newsapi.org/v2/everything"
                params = {
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": "20",
                    "apiKey": self._newsapi_key,
                }
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    continue
                payload = response.json()
                rows = payload.get("articles") if isinstance(payload, dict) else []
                if not isinstance(rows, list):
                    continue
                for item in rows[:20]:
                    article = self._build_article(
                        source="newsapi",
                        headline=str(item.get("title", "")),
                        url=str(item.get("url", "")),
                        body=str(item.get("description", "") or ""),
                        symbols=[],
                        triggered_at=triggered_at,
                    )
                    if article is not None:
                        articles.append(article)
        return articles

    async def _fetch_asx_announcements(self, *, triggered_at: str) -> list[dict[str, JSONValue]]:
        url = "https://www.asx.com.au/asx/1/company/announcements?count=50&market_sensitive=true"
        headers = {"Accept": "application/json"}
        articles: list[dict[str, JSONValue]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0, headers=headers) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return []
            payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                return []
            for row in rows[:40]:
                issuer = str(row.get("issuer_code", "")).upper().strip()
                if not issuer:
                    continue
                symbol = f"{issuer}.AX"
                article = self._build_article(
                    source="asx_announcement",
                    headline=str(row.get("header", "")),
                    url=str(row.get("url", "")),
                    body=str(row.get("headline", "")),
                    symbols=[symbol],
                    triggered_at=triggered_at,
                )
                if article is not None:
                    articles.append(article)
        return articles

    def _build_scout_articles(
        self,
        *,
        scout_theses: list[dict[str, object]],
        triggered_at: str,
        skipped_reasons: dict[str, int],
    ) -> list[dict[str, JSONValue]]:
        articles: list[dict[str, JSONValue]] = []
        for thesis in scout_theses[:8]:
            ticker = str(thesis.get("ticker", "")).upper()
            if ticker not in _WATCHLIST_US:
                _increment(skipped_reasons, "scout_non_watchlist")
                continue
            confidence = _as_float(thesis.get("confidence"))
            score = round(_as_float(thesis.get("thesis_score")) * 100.0, 2)
            sentiment = round(_as_float(thesis.get("avg_sentiment")), 4)
            source = f"scout_thesis_{str(thesis.get('source', 'scout')).lower()}"
            articles.append(
                {
                    "signal_id": f"news.article:{source}:{ticker}:{hashlib.md5(str(thesis.get('id', ticker)).encode('utf-8')).hexdigest()[:8]}",
                    "source": source,
                    "ticker": ticker,
                    "headline": f"{ticker} thesis promoted to market brief",
                    "url": "",
                    "sentiment": sentiment,
                    "confidence": round(confidence, 4),
                    "symbols": [ticker],
                    "is_price_sensitive": score >= 70.0,
                    "raw_score": score,
                    "scanned_at": triggered_at,
                }
            )
        return articles

    def _build_article(
        self,
        *,
        source: str,
        headline: str,
        url: str,
        body: str,
        symbols: list[str],
        triggered_at: str,
    ) -> dict[str, JSONValue] | None:
        normalized_headline = " ".join(headline.split()).strip()
        if not normalized_headline:
            return None
        extracted_symbols = self._extract_symbols(f"{normalized_headline} {body}")
        known_symbols = [symbol.upper() for symbol in symbols if symbol.upper() in _WATCHLIST_ALL]
        tickers = _dedupe_strings([*known_symbols, *extracted_symbols])
        sentiment, confidence = self._score_sentiment(f"{normalized_headline} {body}")
        primary = tickers[0] if tickers else ""
        article_id = hashlib.md5(f"{source}:{url}:{normalized_headline[:160]}".encode("utf-8")).hexdigest()[:16]
        return cast(dict[str, JSONValue], {
            "signal_id": f"news.article:{source}:{primary}:{article_id}",
            "source": source,
            "ticker": primary,
            "headline": normalized_headline[:500],
            "url": url,
            "sentiment": round(sentiment, 4),
            "confidence": round(confidence, 4),
            "symbols": tickers[:5],
            "is_price_sensitive": self._is_price_sensitive(f"{normalized_headline} {body}"),
            "raw_score": round(sentiment * confidence * 100.0, 2),
            "scanned_at": triggered_at,
        })

    def _extract_symbols(self, text: str) -> list[str]:
        upper_text = text.upper()
        words = set(re.findall(r"\b([A-Z]{2,5})\b", upper_text))
        matched = [word for word in words if word in _WATCHLIST_US]
        for asx_symbol in _WATCHLIST_ASX:
            issuer = asx_symbol.removesuffix(".AX")
            if f"ASX:{issuer}" in upper_text and asx_symbol not in matched:
                matched.append(asx_symbol)
        lower_text = text.lower()
        for name, ticker in _COMPANY_NAMES.items():
            if name in lower_text and ticker not in matched:
                matched.append(ticker)
        return matched

    def _score_sentiment(self, text: str) -> tuple[float, float]:
        lowered = text.lower()
        total_score = 0.0
        hits = 0
        for word, weight in _POSITIVE_WORDS.items():
            count = lowered.count(word)
            if count > 0:
                total_score += weight * min(count, 3)
                hits += 1
        for word, weight in _NEGATIVE_WORDS.items():
            count = lowered.count(word)
            if count > 0:
                total_score += weight * min(count, 3)
                hits += 1
        if hits == 0:
            return 0.18, 0.58
        normalized = max(-1.0, min(1.0, total_score / (hits * 3.0)))
        confidence = min(0.95, 0.55 + (hits * 0.08))
        return normalized, confidence

    def _is_price_sensitive(self, text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in _PRICE_SENSITIVE_TERMS)

    def _network_enabled(self) -> bool:
        if self._disable_live_fetch:
            return False
        if os.getenv("PYTEST_CURRENT_TEST"):
            return False
        return True


def _clean_google_title(title: str) -> str:
    normalized = " ".join(title.split()).strip()
    if " - " in normalized:
        return normalized.rsplit(" - ", 1)[0]
    return normalized


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.upper().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _dedupe_articles(articles: list[dict[str, JSONValue]]) -> list[dict[str, JSONValue]]:
    seen: set[str] = set()
    deduped: list[dict[str, JSONValue]] = []
    for article in articles:
        signature = (
            f"{article.get('source', '')}:"
            f"{article.get('ticker', '')}:"
            f"{article.get('headline', '')}:"
            f"{article.get('url', '')}"
        )
        digest = hashlib.md5(signature.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        deduped.append(article)
    return deduped


def _symbols_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    symbols: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.upper().strip()
            if normalized:
                symbols.append(normalized)
    return symbols


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0
