from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.runtime_config.service import RuntimeConfigService


class MarketDataService:
    _cache: dict[str, tuple[datetime, Any]] = {}
    _MISSING = object()

    async def fetch_daily_bars(self, symbol: str, *, range_name: str = "1y") -> list[dict[str, object]]:
        cache_key = f"bars:{symbol.upper()}:{range_name}"
        cached = self._get_cached(cache_key, ttl_seconds=900)
        if isinstance(cached, list):
            return cached
        if not await self._network_enabled():
            return []
        encoded = urllib.parse.quote(symbol.upper(), safe=".^")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        params = {"range": range_name, "interval": "1d", "includePrePost": "false", "events": "div,splits"}
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        rows = _parse_yahoo_bars(payload)
        self._set_cache(cache_key, rows)
        return rows

    async def fetch_earnings_date(self, symbol: str) -> str | None:
        normalized = symbol.upper()
        cache_key = f"earnings:{normalized}"
        cached = self._get_cached(cache_key, ttl_seconds=43200)
        if cached is None or isinstance(cached, str):
            return cached
        if not await self._network_enabled():
            return None
        if normalized.endswith(".AX") or normalized in {"SPY", "QQQ", "DIA", "IWM", "GLD", "LIT", "GDX", "GDXJ", "COPX"}:
            self._set_cache(cache_key, None)
            return None
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        settings = await self._settings()
        max_days = int(settings["earnings_lookahead_days"])
        request_timeout = float(settings["earnings_timeout_seconds"])
        error_streak = 0
        async with httpx.AsyncClient(timeout=request_timeout, follow_redirects=True, headers=headers) as client:
            for day_offset in range(max(1, max_days)):
                check_date = (datetime.now(timezone.utc) + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                url = f"https://api.nasdaq.com/api/calendar/earnings?date={check_date}"
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    error_streak += 1
                    if error_streak >= 2:
                        break
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    continue
                error_streak = 0
                rows = ((payload.get("data") or {}).get("rows") or []) if isinstance(payload, dict) else []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("symbol", "")).upper() == normalized:
                        self._set_cache(cache_key, check_date)
                        return check_date
        self._set_cache(cache_key, None)
        return None

    async def _settings(self) -> dict[str, object]:
        runtime = RuntimeConfigService()
        disable_live_fetch = await runtime.resolve_bool("intel.disable_live_fetch")
        earnings_lookahead_days = await runtime.resolve("intel.earnings_lookahead_days")
        earnings_timeout_seconds = await runtime.resolve_float("intel.earnings_timeout_seconds")
        return {
            "disable_live_fetch": bool(disable_live_fetch.value),
            "earnings_lookahead_days": max(1, int(earnings_lookahead_days.value)),
            "earnings_timeout_seconds": max(0.5, float(earnings_timeout_seconds.value)),
        }

    async def _network_enabled(self) -> bool:
        if (await self._settings())["disable_live_fetch"]:
            return False
        if os.getenv("PYTEST_CURRENT_TEST"):
            return False
        return True

    @classmethod
    def _get_cached(cls, key: str, *, ttl_seconds: int) -> Any:
        cached = cls._cache.get(key)
        if cached is None:
            return cls._MISSING
        fetched_at, payload = cached
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() > ttl_seconds:
            return cls._MISSING
        return payload

    @classmethod
    def _set_cache(cls, key: str, payload: Any) -> None:
        cls._cache[key] = (datetime.now(timezone.utc), payload)

def _parse_yahoo_bars(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return []
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return []
    first = results[0]
    if not isinstance(first, dict):
        return []
    timestamps = first.get("timestamp")
    indicators = first.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return []
    quote_rows = indicators.get("quote")
    if not isinstance(quote_rows, list) or not quote_rows:
        return []
    quote = quote_rows[0]
    if not isinstance(quote, dict):
        return []
    closes = quote.get("close")
    volumes = quote.get("volume")
    if not isinstance(closes, list):
        return []
    rows: list[dict[str, object]] = []
    for index, close in enumerate(closes):
        if close is None:
            continue
        try:
            close_value = float(close)
        except (TypeError, ValueError):
            continue
        raw_ts = timestamps[index] if index < len(timestamps) else None
        volume_value = volumes[index] if isinstance(volumes, list) and index < len(volumes) else None
        if not isinstance(raw_ts, int):
            continue
        rows.append(
            {
                "close": close_value,
                "volume": int(volume_value) if isinstance(volume_value, (int, float)) else 0,
                "timestamp": datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat(),
            }
        )
    return rows
