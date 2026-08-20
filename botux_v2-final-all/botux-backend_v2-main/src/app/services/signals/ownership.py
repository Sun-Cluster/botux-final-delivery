from __future__ import annotations

from collections.abc import Mapping


_NEWSFEED_SOURCES = {
    "newsapi",
    "alpaca_news",
    "google_news",
    "alpaca_watchlist_news",
    "finviz",
    "benzinga",
    "polygon_news",
    "rss_multi",
    "gnews",
}

_AUSMINE_SOURCES = {
    "ausmine",
    "ausmining",
    "permit_scanner",
    "nugget",
    "nugget_bot",
    "miner_bot",
}

_SCOUT_EXPLICIT_SOURCES = {
    "scout_thesis_us",
    "scout_thesis_asx",
    "scout_rss_multi",
    "scout_finviz",
    "scout_sec_edgar",
    "scout_gnews",
    "scout_coingecko",
    "scout_crypto_fear_greed",
    "scout_google_trends",
    "scout_finnhub",
    "scout_fred",
    "scout_reddit",
}

_SELF_EXECUTION_SOURCES: dict[str, str] = {
    "tradecopy": "copycat",
    "copycat": "copycat",
    "copy_trader": "copycat",
    "13f": "copycat",
    "options": "gambler",
    "gambler": "gambler",
    "gambler_bot": "gambler",
    "swingtrade": "drifter",
    "drifter": "drifter",
    "drifter_bot": "drifter",
    "evo_catalyst": "evo_catalyst",
    "evo": "evo_catalyst",
    "evo_intel": "evo_catalyst",
    "evo_quality": "evo_catalyst",
}

_VALID_EXECUTION_BOTS = {"turbo", "drifter", "gambler", "copycat", "nugget_bot", "evo_catalyst"}


def build_signal_ownership(
    *,
    source: str,
    symbol: str,
    lane_hint: str | None,
    strategy_hint: str | None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, str]:
    raw_metadata = metadata or {}
    explicit_execution = _normalized_text(raw_metadata.get("execution_bot_id"))
    if explicit_execution is not None and explicit_execution in _VALID_EXECUTION_BOTS:
        execution_bot_id = explicit_execution
    else:
        execution_bot_id = _infer_execution_bot_id(
            source=source,
            symbol=symbol,
            lane_hint=lane_hint,
            strategy_hint=strategy_hint,
            metadata=raw_metadata,
        )
    explicit_origin = _normalized_text(raw_metadata.get("origin_bot_id"))
    origin_bot_id = explicit_origin or _infer_origin_bot_id(
        source=source,
        lane_hint=lane_hint,
        strategy_hint=strategy_hint,
    )
    return {
        "origin_bot_id": origin_bot_id,
        "execution_bot_id": execution_bot_id,
        "bot_id": execution_bot_id,
    }


def infer_execution_bot_id(signal: object) -> str:
    metadata = _signal_metadata(signal)
    explicit_execution = _normalized_text(metadata.get("execution_bot_id"))
    if explicit_execution is not None and explicit_execution in _VALID_EXECUTION_BOTS:
        return explicit_execution
    explicit_bot = _normalized_text(metadata.get("bot_id"))
    if explicit_bot is not None and explicit_bot in _VALID_EXECUTION_BOTS:
        return explicit_bot
    return _infer_execution_bot_id(
        source=_normalized_text(_signal_value(signal, "source")) or "unknown",
        symbol=_normalized_text(_signal_value(signal, "symbol")) or "",
        lane_hint=_normalized_text(_signal_value(signal, "lane_hint")),
        strategy_hint=_normalized_text(_signal_value(signal, "strategy_hint")),
        metadata=metadata,
    )


def infer_origin_bot_id(signal: object) -> str:
    metadata = _signal_metadata(signal)
    explicit_origin = _normalized_text(metadata.get("origin_bot_id"))
    if explicit_origin:
        return explicit_origin
    return _infer_origin_bot_id(
        source=_normalized_text(_signal_value(signal, "source")) or "unknown",
        lane_hint=_normalized_text(_signal_value(signal, "lane_hint")),
        strategy_hint=_normalized_text(_signal_value(signal, "strategy_hint")),
    )


def _infer_execution_bot_id(
    *,
    source: str,
    symbol: str,
    lane_hint: str | None,
    strategy_hint: str | None,
    metadata: Mapping[str, object],
) -> str:
    normalized_source = source.strip().lower()
    normalized_symbol = symbol.strip().upper()
    if normalized_source in _SELF_EXECUTION_SOURCES:
        return _SELF_EXECUTION_SOURCES[normalized_source]
    if normalized_source in _AUSMINE_SOURCES:
        return "nugget_bot"
    if normalized_source == "asx_announcement":
        return "nugget_bot" if normalized_symbol.endswith(".AX") else "turbo"
    if normalized_source in _NEWSFEED_SOURCES:
        return "nugget_bot" if normalized_symbol.endswith(".AX") else "turbo"
    if normalized_source in _SCOUT_EXPLICIT_SOURCES or normalized_source.startswith("scout_"):
        return "turbo"
    normalized_lane = (lane_hint or "").strip().lower()
    normalized_strategy = (strategy_hint or "").strip().lower()
    for candidate in (normalized_lane, normalized_strategy):
        if candidate in _SELF_EXECUTION_SOURCES:
            return _SELF_EXECUTION_SOURCES[candidate]
    raw_market = _normalized_text(metadata.get("market"))
    if raw_market == "asx_equities" or normalized_symbol.endswith(".AX"):
        return "nugget_bot"
    return "unknown"


def _infer_origin_bot_id(*, source: str, lane_hint: str | None, strategy_hint: str | None) -> str:
    normalized_source = source.strip().lower()
    if normalized_source in _AUSMINE_SOURCES:
        return "ausmine_intel"
    if normalized_source == "asx_announcement":
        return "newsfeed_intel"
    if normalized_source in _NEWSFEED_SOURCES:
        return "newsfeed_intel"
    if normalized_source in _SCOUT_EXPLICIT_SOURCES or normalized_source.startswith("scout_"):
        return "scout_engine"
    normalized_lane = (lane_hint or "").strip().lower()
    normalized_strategy = (strategy_hint or "").strip().lower()
    if normalized_lane == "scout" or normalized_strategy.startswith("scout"):
        return "scout_engine"
    if normalized_lane == "news" or normalized_strategy == "newsfeed_intel":
        return "newsfeed_intel"
    if normalized_lane == "ausmine":
        return "ausmine_intel"
    return normalized_source or "unknown"


def _signal_value(signal: object, key: str) -> object:
    if hasattr(signal, key):
        return getattr(signal, key)
    if isinstance(signal, Mapping):
        return signal.get(key)
    return None


def _signal_metadata(signal: object) -> dict[str, object]:
    raw = _signal_value(signal, "metadata")
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _normalized_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None
