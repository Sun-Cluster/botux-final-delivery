from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.services.signals.ownership import infer_execution_bot_id
from infra.brokers.base import BrokerPort


@dataclass(frozen=True)
class BrokerExecutionProfile:
    bot_id: str
    market: str
    primary_broker: str
    allowed_brokers: tuple[str, ...]
    order_types_required: tuple[str, ...]
    preferred_order_type: str


@dataclass(frozen=True)
class BrokerRoute:
    broker_name: str
    broker: BrokerPort
    market: str
    order_type: str
    bot_id: str
    allowed_brokers: tuple[str, ...]
    order_types_required: tuple[str, ...]
    route_reason: str


BROKER_CAPABILITIES: dict[str, dict[str, set[str]]] = {
    "alpaca": {
        "markets": {"us_equities", "options_us", "crypto"},
        "order_types": {"market", "limit", "bracket"},
    },
    "ibkr": {
        "markets": {"us_equities", "asx_equities", "options_us"},
        "order_types": {"market", "limit"},
    },
}

EXECUTION_PROFILES: dict[str, BrokerExecutionProfile] = {
    "turbo": BrokerExecutionProfile(
        bot_id="turbo",
        market="us_equities",
        primary_broker="alpaca",
        allowed_brokers=("alpaca",),
        order_types_required=("market", "bracket"),
        preferred_order_type="bracket",
    ),
    "drifter": BrokerExecutionProfile(
        bot_id="drifter",
        market="us_equities",
        primary_broker="alpaca",
        allowed_brokers=("alpaca",),
        order_types_required=("market", "bracket"),
        preferred_order_type="bracket",
    ),
    "swingtrade": BrokerExecutionProfile(
        bot_id="drifter",
        market="us_equities",
        primary_broker="alpaca",
        allowed_brokers=("alpaca",),
        order_types_required=("market", "bracket"),
        preferred_order_type="bracket",
    ),
    "gambler": BrokerExecutionProfile(
        bot_id="gambler",
        market="options_us",
        primary_broker="alpaca",
        allowed_brokers=("alpaca",),
        order_types_required=("market", "limit"),
        preferred_order_type="limit",
    ),
    "copycat": BrokerExecutionProfile(
        bot_id="copycat",
        market="us_equities",
        primary_broker="alpaca",
        allowed_brokers=("alpaca",),
        order_types_required=("market", "limit"),
        preferred_order_type="limit",
    ),
    "nugget_bot": BrokerExecutionProfile(
        bot_id="nugget_bot",
        market="asx_equities",
        primary_broker="ibkr",
        allowed_brokers=("ibkr",),
        order_types_required=("market", "limit"),
        preferred_order_type="limit",
    ),
    "evo_catalyst": BrokerExecutionProfile(
        bot_id="evo_catalyst",
        market="asx_equities",
        primary_broker="ibkr",
        allowed_brokers=("ibkr",),
        order_types_required=("market",),
        preferred_order_type="market",
    ),
}

BOT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("turbo", ("turbo",)),
    ("swingtrade", ("swingtrade", "drifter", "swing")),
    ("gambler", ("gambler", "options")),
    ("copycat", ("copycat", "tradecopy", "copy")),
    ("nugget_bot", ("ausmining", "nugget", "miner")),
    ("evo_catalyst", ("evo_catalyst", "evo", "catalyst")),
)


class BrokerRouter:
    def __init__(self, default_broker: BrokerPort, brokers: dict[str, BrokerPort] | None = None) -> None:
        self.default_broker = default_broker
        self._brokers: dict[str, BrokerPort] = brokers or {}
        self._default_broker_name = self._infer_default_broker_name(default_broker)

    @property
    def default_broker_name(self) -> str:
        return self._default_broker_name

    def set_default_broker(self, name: str) -> None:
        normalized = name.strip().lower()
        broker = self.get(normalized)
        if broker is None:
            return
        self.default_broker = broker
        self._default_broker_name = normalized

    def plan(self, signal: object) -> BrokerRoute:
        bot_id = infer_bot_id(signal)
        profile = EXECUTION_PROFILES.get(bot_id)
        market = infer_market(signal, bot_id=bot_id, profile=profile)
        order_type = infer_order_type(signal, bot_id=bot_id, profile=profile)
        preferred_broker_name = profile.primary_broker if profile is not None else None
        allowed_brokers = profile.allowed_brokers if profile is not None else tuple(
            self._eligible_brokers(market=market, order_type=order_type)
        )
        broker_name, reason = self._pick_broker_name(
            preferred_broker_name=preferred_broker_name,
            allowed_brokers=allowed_brokers,
            market=market,
            order_type=order_type,
            fallback_reason="default_router",
        )
        broker = self.get(broker_name) or self.default_broker
        return BrokerRoute(
            broker_name=broker_name,
            broker=broker,
            market=market,
            order_type=order_type,
            bot_id=bot_id,
            allowed_brokers=allowed_brokers,
            order_types_required=profile.order_types_required if profile is not None else (order_type,),
            route_reason=reason,
        )

    def resolve(self, signal: object) -> BrokerPort:
        return self.plan(signal).broker

    def resolve_name(self, signal: object) -> str:
        return self.plan(signal).broker_name

    def list_brokers(self) -> list[str]:
        if not self._brokers:
            return ["default"]
        return sorted(self._brokers.keys())

    def get(self, name: str) -> BrokerPort | None:
        return self._brokers.get(name.strip().lower())

    def _pick_broker_name(
        self,
        *,
        preferred_broker_name: str | None,
        allowed_brokers: tuple[str, ...],
        market: str,
        order_type: str,
        fallback_reason: str,
    ) -> tuple[str, str]:
        if preferred_broker_name is not None and self._supports(
            preferred_broker_name,
            market=market,
            order_type=order_type,
        ):
            broker = self.get(preferred_broker_name)
            if broker is not None:
                return preferred_broker_name, f"profile:{preferred_broker_name}"

        for broker_name in allowed_brokers:
            if self._supports(broker_name, market=market, order_type=order_type) and self.get(broker_name) is not None:
                return broker_name, f"capability:{broker_name}"

        if self._supports(self._default_broker_name, market=market, order_type=order_type):
            return self._default_broker_name, fallback_reason

        eligible = self._eligible_brokers(market=market, order_type=order_type)
        for broker_name in eligible:
            if self.get(broker_name) is not None:
                return broker_name, f"market_fit:{broker_name}"
        return self._default_broker_name, fallback_reason

    def _eligible_brokers(self, *, market: str, order_type: str) -> list[str]:
        eligible: list[str] = []
        for broker_name in self.list_brokers():
            if self._supports(broker_name, market=market, order_type=order_type):
                eligible.append(broker_name)
        return eligible

    def _supports(self, broker_name: str, *, market: str, order_type: str) -> bool:
        capabilities = BROKER_CAPABILITIES.get(broker_name, {})
        markets = capabilities.get("markets", set())
        order_types = capabilities.get("order_types", set())
        return market in markets and order_type in order_types

    def _infer_default_broker_name(self, broker: BrokerPort) -> str:
        for name, candidate in self._brokers.items():
            if candidate is broker:
                return name
        return "default"


def infer_bot_id(signal: object) -> str:
    owned_bot_id = infer_execution_bot_id(signal)
    if owned_bot_id not in {"unknown", "manual"}:
        return owned_bot_id
    text = " ".join(
        str(part)
        for part in (
            _signal_value(signal, "signal_id"),
            _signal_value(signal, "source"),
            _signal_value(signal, "lane_hint"),
            _signal_value(signal, "strategy_hint"),
        )
        if part
    ).lower()
    for bot_id, aliases in BOT_ALIASES:
        if any(alias in text for alias in aliases):
            return bot_id
    return "manual" if "manual" in text else "unknown"


def infer_market(
    signal: object,
    *,
    bot_id: str,
    profile: BrokerExecutionProfile | None = None,
) -> str:
    metadata = _signal_metadata(signal)
    raw_market = metadata.get("market")
    if isinstance(raw_market, str) and raw_market.strip():
        return raw_market.strip().lower()
    if profile is not None:
        return profile.market
    symbol = str(_signal_value(signal, "symbol") or "").upper()
    if symbol.endswith(".AX"):
        return "asx_equities"
    if bot_id == "gambler":
        return "options_us"
    return "us_equities"


def infer_order_type(
    signal: object,
    *,
    bot_id: str,
    profile: BrokerExecutionProfile | None = None,
) -> str:
    metadata = _signal_metadata(signal)
    raw_order_type = metadata.get("order_type")
    if isinstance(raw_order_type, str) and raw_order_type.strip():
        return raw_order_type.strip().lower()
    if profile is not None:
        return profile.preferred_order_type
    if bot_id == "gambler":
        return "limit"
    return "market"


def _signal_value(signal: object, key: str) -> object:
    if hasattr(signal, key):
        return getattr(signal, key)
    if isinstance(signal, Mapping):
        return signal.get(key)
    return None


def _signal_metadata(signal: object) -> dict[str, object]:
    raw = _signal_value(signal, "metadata")
    if isinstance(raw, dict):
        return raw
    return {}
