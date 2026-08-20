from __future__ import annotations

import os

from dataclasses import dataclass

from tortoise.backends.base.client import BaseDBAsyncClient

from db.repositories._common import JSONValue
from db.repositories.system_configs_repo import SystemConfigsRepository


@dataclass(frozen=True)
class RuntimeConfigDefinition:
    key: str
    env_name: str
    value_type: str
    default: JSONValue
    description: str
    scope: str = "global"
    secret: bool = False
    group: str = "runtime"
    label: str | None = None


@dataclass(frozen=True)
class RuntimeConfigValue:
    key: str
    value: JSONValue
    value_type: str
    origin: str
    env_name: str
    description: str
    scope: str


RUNTIME_CONFIG_DEFINITIONS: dict[str, RuntimeConfigDefinition] = {
    "bypass.council": RuntimeConfigDefinition(
        key="bypass.council",
        env_name="BOTUX_BYPASS_COUNCIL",
        value_type="bool",
        default=False,
        description="Operator/test override to bypass council deliberation.",
        group="execution",
        label="Bypass Council",
    ),
    "signal.max_retries": RuntimeConfigDefinition(
        key="signal.max_retries",
        env_name="BOTUX_SIGNAL_MAX_RETRIES",
        value_type="int",
        default=3,
        description="Maximum retry attempts for failed signals.",
        group="execution",
        label="Signal Max Retries",
    ),
    "broker.alpaca.api_key": RuntimeConfigDefinition(
        key="broker.alpaca.api_key",
        env_name="ALPACA_API_KEY",
        value_type="str",
        default="",
        description="Alpaca API key used for account, quotes, and order routing.",
        secret=True,
        group="broker_alpaca",
        label="API Key",
    ),
    "broker.alpaca.secret_key": RuntimeConfigDefinition(
        key="broker.alpaca.secret_key",
        env_name="ALPACA_SECRET_KEY",
        value_type="str",
        default="",
        description="Alpaca secret key paired with the API key.",
        secret=True,
        group="broker_alpaca",
        label="Secret Key",
    ),
    "broker.alpaca.base_url": RuntimeConfigDefinition(
        key="broker.alpaca.base_url",
        env_name="ALPACA_BASE_URL",
        value_type="str",
        default="https://paper-api.alpaca.markets",
        description="Alpaca trading API base URL.",
        group="broker_alpaca",
        label="Trading API URL",
    ),
    "broker.alpaca.data_url": RuntimeConfigDefinition(
        key="broker.alpaca.data_url",
        env_name="ALPACA_DATA_URL",
        value_type="str",
        default="https://data.alpaca.markets",
        description="Alpaca market data API base URL.",
        group="broker_alpaca",
        label="Data API URL",
    ),
    "broker.alpaca.timeout_seconds": RuntimeConfigDefinition(
        key="broker.alpaca.timeout_seconds",
        env_name="ALPACA_TIMEOUT_SECONDS",
        value_type="float",
        default=15.0,
        description="HTTP timeout for Alpaca requests.",
        group="broker_alpaca",
        label="Timeout Seconds",
    ),
    "broker.alpaca.real_enabled": RuntimeConfigDefinition(
        key="broker.alpaca.real_enabled",
        env_name="BOTUX_ALPACA_REAL_ENABLED",
        value_type="bool",
        default=True,
        description="Enable real Alpaca adapter connectivity.",
        group="broker_alpaca",
        label="Enable Alpaca",
    ),
    "broker.ibkr.host": RuntimeConfigDefinition(
        key="broker.ibkr.host",
        env_name="IBKR_HOST",
        value_type="str",
        default="127.0.0.1",
        description="IBKR gateway or TWS host.",
        group="broker_ibkr",
        label="Host",
    ),
    "broker.ibkr.port": RuntimeConfigDefinition(
        key="broker.ibkr.port",
        env_name="IBKR_PORT",
        value_type="int",
        default=4002,
        description="IBKR gateway or TWS port.",
        group="broker_ibkr",
        label="Port",
    ),
    "broker.ibkr.client_id": RuntimeConfigDefinition(
        key="broker.ibkr.client_id",
        env_name="IBKR_CLIENT_ID",
        value_type="int",
        default=1,
        description="IBKR client id for the session.",
        group="broker_ibkr",
        label="Client ID",
    ),
    "broker.ibkr.account_id": RuntimeConfigDefinition(
        key="broker.ibkr.account_id",
        env_name="IBKR_ACCOUNT",
        value_type="str",
        default="",
        description="Optional IBKR account identifier override.",
        group="broker_ibkr",
        label="Account ID",
    ),
    "broker.ibkr.timeout_seconds": RuntimeConfigDefinition(
        key="broker.ibkr.timeout_seconds",
        env_name="IBKR_TIMEOUT_SECONDS",
        value_type="float",
        default=12.0,
        description="Connection timeout for IBKR requests.",
        group="broker_ibkr",
        label="Timeout Seconds",
    ),
    "broker.ibkr.real_enabled": RuntimeConfigDefinition(
        key="broker.ibkr.real_enabled",
        env_name="BOTUX_IBKR_REAL_ENABLED",
        value_type="bool",
        default=False,
        description="Enable real IBKR adapter connectivity.",
        group="broker_ibkr",
        label="Enable IBKR",
    ),
    "broker.default": RuntimeConfigDefinition(
        key="broker.default",
        env_name="BOTUX_BROKER_MODE",
        value_type="str",
        default="alpaca",
        description="Default broker selected for account snapshots and execution surfaces.",
        group="broker_global",
        label="Default Broker",
    ),
    "execution.enforce_exec_guards": RuntimeConfigDefinition(
        key="execution.enforce_exec_guards",
        env_name="BOTUX_ENFORCE_EXEC_GUARDS",
        value_type="bool",
        default=False,
        description="Hard-block orders when execution guardrails fail.",
        group="execution",
        label="Enforce Execution Guards",
    ),
    "execution.max_spread_bps": RuntimeConfigDefinition(
        key="execution.max_spread_bps",
        env_name="BOTUX_MAX_SPREAD_BPS",
        value_type="float",
        default=12.0,
        description="Maximum bid/ask spread in basis points before guard triggers.",
        group="execution",
        label="Max Spread (bps)",
    ),
    "execution.max_trades_per_day": RuntimeConfigDefinition(
        key="execution.max_trades_per_day",
        env_name="BOTUX_MAX_TRADES_PER_DAY",
        value_type="int",
        default=8,
        description="Maximum daily entry orders when routing against live broker accounts.",
        group="execution",
        label="Max Trades Per Day (Live)",
    ),
    "execution.max_trades_per_day_paper": RuntimeConfigDefinition(
        key="execution.max_trades_per_day_paper",
        env_name="BOTUX_MAX_TRADES_PER_DAY_PAPER",
        value_type="int",
        default=50,
        description="Maximum daily entry orders when routing against paper broker accounts.",
        group="execution",
        label="Max Trades Per Day (Paper)",
    ),
    "execution.cooldown_minutes": RuntimeConfigDefinition(
        key="execution.cooldown_minutes",
        env_name="BOTUX_COOLDOWN_MINUTES",
        value_type="int",
        default=20,
        description="Minimum cooldown window between entry attempts for the same symbol.",
        group="execution",
        label="Cooldown Minutes",
    ),
    "execution.max_signal_age_minutes_us": RuntimeConfigDefinition(
        key="execution.max_signal_age_minutes_us",
        env_name="BOTUX_MAX_SIGNAL_AGE_MINUTES_US",
        value_type="int",
        default=30,
        description="Maximum age (minutes) for US signals before execution rejects as stale.",
        group="execution",
        label="Max US Signal Age (min)",
    ),
    "execution.max_signal_age_minutes_asx": RuntimeConfigDefinition(
        key="execution.max_signal_age_minutes_asx",
        env_name="BOTUX_MAX_SIGNAL_AGE_MINUTES_ASX",
        value_type="int",
        default=240,
        description="Maximum age (minutes) for ASX signals before execution rejects as stale.",
        group="execution",
        label="Max ASX Signal Age (min)",
    ),
    "execution.max_signal_price_drift_pct": RuntimeConfigDefinition(
        key="execution.max_signal_price_drift_pct",
        env_name="BOTUX_MAX_SIGNAL_PRICE_DRIFT_PCT",
        value_type="float",
        default=2.0,
        description="Maximum allowed drift percentage between signal reference price and current quote.",
        group="execution",
        label="Max Signal Price Drift %",
    ),
    "risk.max_daily_loss_pct": RuntimeConfigDefinition(
        key="risk.max_daily_loss_pct",
        env_name="MAX_DAILY_LOSS_PCT",
        value_type="float",
        default=0.03,
        description="Maximum intraday loss as a fraction of account equity before halting execution.",
        group="risk",
        label="Max Daily Loss %",
    ),
    "risk.risk_per_trade_pct": RuntimeConfigDefinition(
        key="risk.risk_per_trade_pct",
        env_name="RISK_PER_TRADE_PCT",
        value_type="float",
        default=0.01,
        description="Maximum per-trade risk budget as a fraction of account equity.",
        group="risk",
        label="Risk Per Trade %",
    ),
    "risk.max_position_pct": RuntimeConfigDefinition(
        key="risk.max_position_pct",
        env_name="MAX_POSITION_PCT",
        value_type="float",
        default=0.10,
        description="Maximum position notional as a fraction of account equity.",
        group="risk",
        label="Max Position %",
    ),
    "risk.max_open_positions": RuntimeConfigDefinition(
        key="risk.max_open_positions",
        env_name="MAX_OPEN_POSITIONS",
        value_type="int",
        default=15,
        description="Maximum concurrently open positions allowed for execution.",
        group="risk",
        label="Max Open Positions",
    ),
    "bypass.risk": RuntimeConfigDefinition(
        key="bypass.risk",
        env_name="BOTUX_BYPASS_RISK",
        value_type="bool",
        default=False,
        description="Operator/test override to bypass runtime risk controls in execution path.",
        group="risk",
        label="Bypass Risk Controls",
    ),
    "bypass.market_hours": RuntimeConfigDefinition(
        key="bypass.market_hours",
        env_name="BOTUX_BYPASS_MARKET_HOURS",
        value_type="bool",
        default=False,
        description="Operator/test override to bypass runtime market-hours execution gate.",
        group="execution",
        label="Bypass Market Hours",
    ),
    "bypass.bot_lifecycle": RuntimeConfigDefinition(
        key="bypass.bot_lifecycle",
        env_name="BOTUX_BYPASS_BOT_LIFECYCLE",
        value_type="bool",
        default=False,
        description="Operator/test override to bypass bot lifecycle/profile execution gate.",
        group="execution",
        label="Bypass Bot Lifecycle Gate",
    ),
    "intel.sec_13f_user_agent": RuntimeConfigDefinition(
        key="intel.sec_13f_user_agent",
        env_name="BOTUX_SEC_USER_AGENT",
        value_type="str",
        default="BOTUX tradecopy support@example.com",
        description="User-Agent header used when BOTUX fetches SEC EDGAR 13F data.",
        group="data_sources",
        label="SEC User Agent",
    ),
    "intel.sec_13f_timeout_seconds": RuntimeConfigDefinition(
        key="intel.sec_13f_timeout_seconds",
        env_name="BOTUX_SEC_TIMEOUT_SECONDS",
        value_type="float",
        default=8.0,
        description="HTTP timeout for SEC EDGAR 13F requests.",
        group="data_sources",
        label="SEC Timeout Seconds",
    ),
    "intel.sec_13f_concurrency": RuntimeConfigDefinition(
        key="intel.sec_13f_concurrency",
        env_name="BOTUX_SEC_CONCURRENCY",
        value_type="int",
        default=3,
        description="Maximum concurrent SEC EDGAR 13F fund fetches.",
        group="data_sources",
        label="SEC Concurrency",
    ),
    "intel.sec_13f_new_filing_lookback_days": RuntimeConfigDefinition(
        key="intel.sec_13f_new_filing_lookback_days",
        env_name="BOTUX_SEC_NEW_FILING_LOOKBACK_DAYS",
        value_type="int",
        default=7,
        description="Number of days a 13F filing is considered recent for tradecopy scan status.",
        group="data_sources",
        label="New Filing Lookback Days",
    ),
    "intel.disable_live_fetch": RuntimeConfigDefinition(
        key="intel.disable_live_fetch",
        env_name="BOTUX_DISABLE_LIVE_INTEL_FETCH",
        value_type="bool",
        default=False,
        description="Disable live third-party market and intelligence fetches.",
        group="data_sources",
        label="Disable Live Fetch",
    ),
    "intel.earnings_lookahead_days": RuntimeConfigDefinition(
        key="intel.earnings_lookahead_days",
        env_name="BOTUX_EARNINGS_LOOKAHEAD_DAYS",
        value_type="int",
        default=5,
        description="How many future days to scan for earnings dates.",
        group="data_sources",
        label="Earnings Lookahead Days",
    ),
    "intel.earnings_timeout_seconds": RuntimeConfigDefinition(
        key="intel.earnings_timeout_seconds",
        env_name="BOTUX_EARNINGS_TIMEOUT_SECONDS",
        value_type="float",
        default=2.5,
        description="HTTP timeout for earnings calendar requests.",
        group="data_sources",
        label="Earnings Timeout Seconds",
    ),
    "intel.news_api_key": RuntimeConfigDefinition(
        key="intel.news_api_key",
        env_name="NEWS_API_KEY",
        value_type="str",
        default="",
        description="NewsAPI key for supplemental news ingestion.",
        secret=True,
        group="data_sources",
        label="NewsAPI Key",
    ),
    "scheduler.portfolio_snapshot_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.portfolio_snapshot_interval_seconds",
        env_name="BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS",
        value_type="int",
        default=60,
        description="Portfolio snapshot scheduler interval in seconds.",
        group="scheduler",
        label="Portfolio Snapshot Interval",
    ),
    "scheduler.reconcile_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.reconcile_interval_seconds",
        env_name="BOTUX_RECONCILE_INTERVAL_SECONDS",
        value_type="int",
        default=0,
        description="Reconcile scheduler interval in seconds. Set 0 to disable.",
        group="scheduler",
        label="Reconcile Interval",
    ),
    "scheduler.news_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.news_scan_interval_seconds",
        env_name="BOTUX_NEWS_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=180,
        description="News scan scheduler interval in seconds.",
        group="scheduler",
        label="News Scan Interval",
    ),
    "scheduler.signal_broadcast_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.signal_broadcast_interval_seconds",
        env_name="BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS",
        value_type="int",
        default=60,
        description="Signal broadcast scheduler interval in seconds.",
        group="scheduler",
        label="Signal Broadcast Interval",
    ),
    "scheduler.execution_loop_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.execution_loop_interval_seconds",
        env_name="BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS",
        value_type="int",
        default=30,
        description="Execution loop scheduler interval in seconds.",
        group="scheduler",
        label="Execution Loop Interval",
    ),
    "scheduler.risk_cycle_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.risk_cycle_interval_seconds",
        env_name="BOTUX_RISK_CYCLE_INTERVAL_SECONDS",
        value_type="int",
        default=120,
        description="Risk cycle scheduler interval in seconds.",
        group="scheduler",
        label="Risk Cycle Interval",
    ),
    "scheduler.position_monitor_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.position_monitor_interval_seconds",
        env_name="BOTUX_POSITION_MONITOR_INTERVAL_SECONDS",
        value_type="int",
        default=60,
        description="Position monitor and lane exit scheduler interval in seconds.",
        group="scheduler",
        label="Position Monitor Interval",
    ),
    "scheduler.scout_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.scout_scan_interval_seconds",
        env_name="BOTUX_SCOUT_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="Scout scan scheduler interval in seconds.",
        group="scheduler",
        label="Scout Scan Interval",
    ),
    "scheduler.tradecopy_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.tradecopy_scan_interval_seconds",
        env_name="BOTUX_TRADECOPY_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="Tradecopy scan scheduler interval in seconds.",
        group="scheduler",
        label="Tradecopy Scan Interval",
    ),
    "scheduler.options_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.options_scan_interval_seconds",
        env_name="BOTUX_OPTIONS_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="Options scan scheduler interval in seconds.",
        group="scheduler",
        label="Options Scan Interval",
    ),
    "scheduler.swingtrade_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.swingtrade_scan_interval_seconds",
        env_name="BOTUX_SWINGTRADE_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="SwingTrade scan scheduler interval in seconds.",
        group="scheduler",
        label="SwingTrade Scan Interval",
    ),
    "scheduler.miner_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.miner_scan_interval_seconds",
        env_name="BOTUX_MINER_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="AusMine scan scheduler interval in seconds.",
        group="scheduler",
        label="AusMine Scan Interval",
    ),
    "scheduler.evo_scan_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.evo_scan_interval_seconds",
        env_name="BOTUX_EVO_SCAN_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="Evo scan scheduler interval in seconds.",
        group="scheduler",
        label="Evo Scan Interval",
    ),
    "scheduler.runtime_proof_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.runtime_proof_interval_seconds",
        env_name="BOTUX_RUNTIME_PROOF_INTERVAL_SECONDS",
        value_type="int",
        default=900,
        description="Runtime proof pack scheduler interval in seconds.",
        group="scheduler",
        label="Runtime Proof Interval",
    ),
    "scheduler.autopilot_enabled": RuntimeConfigDefinition(
        key="scheduler.autopilot_enabled",
        env_name="BOTUX_AUTOPILOT_ENABLED",
        value_type="bool",
        default=True,
        description="Enable autopilot scheduler job.",
        group="scheduler",
        label="Autopilot Enabled",
    ),
    "scheduler.autopilot_interval_seconds": RuntimeConfigDefinition(
        key="scheduler.autopilot_interval_seconds",
        env_name="BOTUX_AUTOPILOT_INTERVAL_SECONDS",
        value_type="int",
        default=300,
        description="Autopilot scheduler interval in seconds.",
        group="scheduler",
        label="Autopilot Interval",
    ),
}


class RuntimeConfigService:
    def __init__(self, connection: BaseDBAsyncClient | None = None) -> None:
        self._repo = SystemConfigsRepository(connection=connection)

    async def resolve(self, key: str) -> RuntimeConfigValue:
        definition = _definition(key)
        record = await self._repo.get_by_key(key)
        if record is not None:
            return RuntimeConfigValue(
                key=key,
                value=_coerce(record.get("value"), definition=definition),
                value_type=definition.value_type,
                origin="db",
                env_name=definition.env_name,
                description=definition.description,
                scope=definition.scope,
            )
        return RuntimeConfigValue(
            key=key,
            value=definition.default,
            value_type=definition.value_type,
            origin="default",
            env_name=definition.env_name,
            description=definition.description,
            scope=definition.scope,
        )

    async def resolve_bool(self, key: str) -> RuntimeConfigValue:
        value = await self.resolve(key)
        return RuntimeConfigValue(
            key=value.key,
            value=bool(value.value),
            value_type=value.value_type,
            origin=value.origin,
            env_name=value.env_name,
            description=value.description,
            scope=value.scope,
        )

    async def resolve_float(self, key: str) -> RuntimeConfigValue:
        value = await self.resolve(key)
        try:
            normalized = float(value.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            normalized = float(_definition(key).default)  # type: ignore[arg-type]
        return RuntimeConfigValue(
            key=value.key,
            value=normalized,
            value_type=value.value_type,
            origin=value.origin,
            env_name=value.env_name,
            description=value.description,
            scope=value.scope,
        )

    async def list_effective(self) -> list[RuntimeConfigValue]:
        return [await self.resolve(key) for key in sorted(RUNTIME_CONFIG_DEFINITIONS.keys())]


def get_runtime_config_definition(key: str) -> RuntimeConfigDefinition:
    return _definition(key)


def coerce_runtime_config_value(key: str, value: JSONValue | object) -> JSONValue:
    return _coerce(value, definition=_definition(key))


def _definition(key: str) -> RuntimeConfigDefinition:
    definition = RUNTIME_CONFIG_DEFINITIONS.get(key)
    if definition is None:
        raise KeyError(f"unsupported runtime config key: {key}")
    return definition


def _coerce(
    value: JSONValue | object,
    *,
    definition: RuntimeConfigDefinition,
) -> JSONValue:
    if definition.value_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if definition.value_type == "float":
        try:
            normalized = float(value)  # type: ignore[arg-type]
            return normalized
        except (TypeError, ValueError):
            return definition.default
    if definition.value_type == "int":
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return definition.default
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
