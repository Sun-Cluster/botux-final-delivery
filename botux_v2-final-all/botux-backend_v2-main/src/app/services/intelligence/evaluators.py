from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from zoneinfo import ZoneInfo

from domain.models.trade_outcome import TradeOutcome

CORR_THRESHOLD = 0.75
CORR_MAX_OVERLAP = 2
CORR_LOOKBACK_DAYS = 60
MAX_PER_SECTOR = 2

PDT_THRESHOLD_USD = 25000.0
PDT_MAX_DAY_TRADES = 3
PDT_RESERVE_TRADES = 1
PDT_WINDOW_DAYS = 5

EARNINGS_BLOCK_DAYS = 3
EARNINGS_CLOSE_DAYS = 2

VIX_BULL = 20.0
VIX_BEAR = 25.0
VIX_CRISIS = 35.0

MARKET_OPEN_ET = 570
AVOID_OPEN_END = 600
BEST_AM_END = 690
BEST_PM_START = 840
BEST_PM_END = 930
AVOID_CLOSE_START = 945
MARKET_CLOSE_ET = 960

SECTOR_MAP: dict[str, str] = {
    "AAPL": "TECH",
    "MSFT": "TECH",
    "GOOGL": "TECH",
    "AMZN": "TECH",
    "NVDA": "TECH",
    "META": "TECH",
    "TSLA": "TECH",
    "AMD": "SEMI",
    "INTC": "SEMI",
    "MU": "SEMI",
    "AVGO": "SEMI",
    "JPM": "FINANCE",
    "BAC": "FINANCE",
    "GS": "FINANCE",
    "V": "FINANCE",
    "MA": "FINANCE",
    "UNH": "HEALTH",
    "JNJ": "HEALTH",
    "PFE": "HEALTH",
    "XOM": "ENERGY",
    "CVX": "ENERGY",
    "SLB": "ENERGY",
    "BA": "INDUSTRIAL",
    "CAT": "INDUSTRIAL",
    "DIS": "CONSUMER",
    "NFLX": "CONSUMER",
    "WMT": "CONSUMER",
    "SPY": "INDEX",
    "QQQ": "INDEX",
    "DIA": "INDEX",
    "IWM": "INDEX",
    "GDX": "MATERIALS",
    "GDXJ": "MATERIALS",
    "COPX": "MATERIALS",
    "LIT": "MATERIALS",
    "GLD": "MATERIALS",
    "SLV": "MATERIALS",
    "BHP.AX": "ASX_MINING",
    "RIO.AX": "ASX_MINING",
    "FMG.AX": "ASX_MINING",
    "PLS.AX": "ASX_MINING",
    "LTR.AX": "ASX_MINING",
    "IGO.AX": "ASX_MINING",
}


@dataclass(frozen=True)
class RegimeEvaluation:
    primary_regime: str
    multiplier: float
    should_trade: bool
    trend: str
    sub_regime: str
    event_density: str
    sector_concentration: str
    bot_eligibility: dict[str, str]


@dataclass(frozen=True)
class TimeWindowEvaluation:
    allowed: bool
    zone: str
    reason: str


def evaluate_regime(
    *,
    vix: float,
    spy_price: float,
    spy_ma200: float,
    trading_halted: bool,
    event_heavy: bool,
    sector_concentration: str,
) -> RegimeEvaluation:
    spy_above_ma = spy_price >= spy_ma200 if spy_ma200 > 0.0 else True
    if trading_halted or vix >= VIX_CRISIS:
        primary = "CRISIS"
        multiplier = 0.0
    elif vix >= VIX_BEAR and not spy_above_ma:
        primary = "BEAR"
        multiplier = 0.25
    elif vix < VIX_BULL and spy_above_ma:
        primary = "BULL"
        multiplier = 1.0
    else:
        primary = "NEUTRAL"
        multiplier = 0.5

    if primary == "CRISIS":
        sub_regime = "volatile"
    elif event_heavy:
        sub_regime = "event-heavy"
    elif vix >= VIX_BEAR:
        sub_regime = "volatile"
    elif abs(spy_price - spy_ma200) / max(spy_ma200, 1.0) >= 0.03:
        sub_regime = "trending"
    else:
        sub_regime = "ranging"

    event_density = "high" if event_heavy else "low"
    trend = "up" if primary == "BULL" else "down" if primary in {"BEAR", "CRISIS"} else "sideways"
    eligibility = _bot_eligibility(primary=primary, sub_regime=sub_regime, sector_concentration=sector_concentration)
    return RegimeEvaluation(
        primary_regime=primary,
        multiplier=multiplier,
        should_trade=primary != "CRISIS",
        trend=trend,
        sub_regime=sub_regime,
        event_density=event_density,
        sector_concentration=sector_concentration,
        bot_eligibility=eligibility,
    )


def evaluate_time_window(now_utc: datetime | None = None) -> TimeWindowEvaluation:
    current = now_utc or datetime.now(timezone.utc)
    et_now = current.astimezone(ZoneInfo("America/New_York"))
    minutes = (et_now.hour * 60) + et_now.minute
    if minutes < MARKET_OPEN_ET:
        return TimeWindowEvaluation(False, "PRE_MARKET", "Before market open")
    if MARKET_OPEN_ET <= minutes < AVOID_OPEN_END:
        return TimeWindowEvaluation(False, "OPEN_AVOID", "Avoiding first 30 min")
    if minutes <= BEST_AM_END:
        return TimeWindowEvaluation(True, "BEST_AM", "Prime morning window")
    if BEST_PM_START <= minutes <= BEST_PM_END:
        return TimeWindowEvaluation(True, "BEST_PM", "Prime afternoon window")
    if minutes >= AVOID_CLOSE_START:
        return TimeWindowEvaluation(False, "CLOSE_AVOID", "Avoiding last 15 min")
    if minutes > MARKET_CLOSE_ET:
        return TimeWindowEvaluation(False, "AFTER_HOURS", "After market close")
    return TimeWindowEvaluation(True, "MIDDAY", "Midday session")


def pearson_correlation(left: list[float], right: list[float]) -> float:
    points = min(len(left), len(right))
    if points < 10:
        return 0.0
    xs = left[-points:]
    ys = right[-points:]
    mean_x = sum(xs) / points
    mean_y = sum(ys) / points
    delta_x = [value - mean_x for value in xs]
    delta_y = [value - mean_y for value in ys]
    numerator = sum(a * b for a, b in zip(delta_x, delta_y))
    denom_x = sqrt(sum(a * a for a in delta_x))
    denom_y = sqrt(sum(b * b for b in delta_y))
    if denom_x == 0.0 or denom_y == 0.0:
        return 0.0
    return numerator / (denom_x * denom_y)


def realized_volatility(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    returns = []
    for index in range(1, len(closes)):
        previous = closes[index - 1]
        current = closes[index]
        if previous <= 0.0:
            continue
        returns.append((current / previous) - 1.0)
    if len(returns) < 2:
        return 0.0
    mean_value = sum(returns) / len(returns)
    variance = sum((value - mean_value) ** 2 for value in returns) / len(returns)
    return sqrt(variance)


def sector_for_symbol(symbol: str) -> str:
    normalized = symbol.upper()
    if normalized in SECTOR_MAP:
        return SECTOR_MAP[normalized]
    if normalized.endswith(".AX"):
        return "ASX_MINING"
    return "OTHER"


def sector_concentration_state(held_symbols: list[str]) -> str:
    counts: dict[str, int] = {}
    total = len(held_symbols)
    if total == 0:
        return "dispersed"
    for symbol in held_symbols:
        sector = sector_for_symbol(symbol)
        counts[sector] = counts.get(sector, 0) + 1
    max_ratio = max(count / total for count in counts.values())
    if max_ratio > 0.25:
        return "over-concentrated"
    if max_ratio > 0.20:
        return "concentrated"
    return "dispersed"


def recent_day_trade_count(outcomes: list[TradeOutcome], *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=7)
    count = 0
    for outcome in outcomes:
        closed_at = outcome.closed_at
        if closed_at is None or closed_at < cutoff:
            continue
        if outcome.outcome.value not in {"win", "loss", "breakeven"}:
            continue
        if outcome.opened_at.date() == closed_at.date():
            count += 1
    return count


def pdt_can_trade(*, equity: float, day_trades_used: int) -> tuple[bool, int, str]:
    if equity >= PDT_THRESHOLD_USD:
        return True, 999, f"Account ${equity:,.0f} - PDT not enforced"
    remaining = max(0, PDT_MAX_DAY_TRADES - day_trades_used)
    safe = remaining - PDT_RESERVE_TRADES
    if safe <= 0:
        return False, remaining, f"PDT BLOCK: {day_trades_used}/{PDT_MAX_DAY_TRADES} used"
    return True, remaining, f"{day_trades_used}/{PDT_MAX_DAY_TRADES} used ({remaining} left)"


def earnings_action(days_until: int | None) -> dict[str, object]:
    if days_until is None:
        return {"near": False, "near_earnings": False, "days_to_earnings": None, "action": "CLEAR", "allowed": True}
    if days_until < 0:
        return {"near": False, "near_earnings": False, "days_to_earnings": days_until, "action": "PAST", "allowed": True}
    if days_until <= EARNINGS_CLOSE_DAYS:
        return {
            "near": True,
            "near_earnings": True,
            "days_to_earnings": days_until,
            "action": "CLOSE_PROFITABLE",
            "allowed": False,
        }
    if days_until <= EARNINGS_BLOCK_DAYS:
        return {
            "near": True,
            "near_earnings": True,
            "days_to_earnings": days_until,
            "action": "BLOCK_ENTRY",
            "allowed": False,
        }
    return {"near": False, "near_earnings": False, "days_to_earnings": days_until, "action": "CLEAR", "allowed": True}


def _bot_eligibility(*, primary: str, sub_regime: str, sector_concentration: str) -> dict[str, str]:
    if primary == "CRISIS":
        return {key: "BLOCKED" for key in ("turbo", "drifter", "gambler", "copycat", "nugget")}
    if primary == "BEAR":
        return {
            "turbo": "MINIMAL",
            "drifter": "BLOCKED",
            "gambler": "REDUCED",
            "copycat": "REDUCED",
            "nugget": "REDUCED",
        }
    if sub_regime == "event-heavy":
        return {
            "turbo": "REDUCED",
            "drifter": "BLOCKED",
            "gambler": "FULL",
            "copycat": "FULL",
            "nugget": "FULL",
        }
    if sub_regime == "volatile":
        return {
            "turbo": "REDUCED",
            "drifter": "BLOCKED",
            "gambler": "FULL",
            "copycat": "REDUCED",
            "nugget": "REDUCED",
        }
    if sector_concentration in {"concentrated", "over-concentrated"}:
        return {
            "turbo": "REDUCED",
            "drifter": "REDUCED",
            "gambler": "FULL",
            "copycat": "REDUCED",
            "nugget": "REDUCED",
        }
    if primary == "BULL" and sub_regime == "trending":
        return {key: "FULL" for key in ("turbo", "drifter", "gambler", "copycat", "nugget")}
    return {
        "turbo": "REDUCED",
        "drifter": "REDUCED",
        "gambler": "FULL",
        "copycat": "FULL",
        "nugget": "FULL",
    }
