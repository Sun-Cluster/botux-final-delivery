from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from loguru import logger
from starlette.responses import StreamingResponse

from api.deps import get_container
from app.services.scan.service import ScanService
from db.repositories.bots_repo import BotsRepository
from db.repositories._common import append_outbox_event
from db.repositories.outbox_repo import OutboxRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from runtime.container import Container

router = APIRouter(tags=["intel-events-compat"])

WATCHLIST_SYMBOLS: tuple[str, ...] = (
    "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD", "INTC", "JPM",
)

SIGNAL_REASON_LABELS: dict[str, str] = {
    "bypass_council": "Approved by bypass rule, so council review was skipped.",
    "bot_disabled": "Rejected because the assigned bot is disabled.",
    "bot_lifecycle_not_executable": "Rejected because the bot is not in an executable lifecycle state.",
    "bot_autopilot_shadow": "Rejected because the bot is currently in autopilot shadow mode.",
    "bot_identity_unresolved": "Rejected because the system could not resolve which bot should own this signal.",
    "bot_profile_missing": "Rejected because no bot profile was found for this signal.",
    "duplicate_within_window": "Rejected because a matching signal was already seen in the dedupe window.",
    "trading_halted": "Rejected because trading is currently halted.",
    "correlation_blocked": "Rejected by correlation risk controls.",
    "pdt_blocked": "Rejected by pattern day trader protection rules.",
    "broker_unavailable": "Execution failed because no broker connection was available for submission.",
    "broker_submit_unconfirmed": "Execution failed because the broker never confirmed receipt of the order.",
    "alpaca_not_configured": "Execution was rejected because Alpaca is not configured.",
    "alpaca_submit_failed": "Execution failed while submitting the order to Alpaca.",
    "alpaca_status_failed": "Execution failed while checking Alpaca order status.",
    "ibkr_not_connected": "Execution was rejected because IBKR is not connected.",
    "ibkr_not_configured": "Execution was rejected because IBKR is not configured.",
    "ibkr_submit_failed": "Execution failed while submitting the order to IBKR.",
    "ibkr_status_failed": "Execution failed while checking IBKR order status.",
    "insufficient_buying_power": "Execution was blocked because the broker account does not have enough buying power.",
    "insufficient_cash": "Execution was blocked because the account cash balance is too low for the order.",
    "wide_spread": "Execution was blocked because the bid/ask spread was too wide.",
    "execution_failed": "Order execution failed after approval.",
    "execution_rejected": "Order execution was rejected after approval.",
    "manual_requeue": "Signal was manually re-queued for processing.",
}


@router.get("/api/agent/feed")
async def get_agent_feed() -> list[dict[str, object]]:
    events = await _safe_recent_outbox(limit=20)
    return events


@router.get("/api/events/feed")
async def get_events_feed() -> dict[str, list[dict[str, object]]]:
    events = await _safe_recent_outbox(limit=50)
    return {"events": events}


@router.get("/api/events/stream")
async def events_stream() -> StreamingResponse:
    async def _generate():
        # Initial heartbeat frame
        yield "data: {}\n\n"
        while True:
            try:
                events = await _safe_recent_outbox(limit=1)
                if events:
                    yield "data: " + json.dumps(events[0], default=str) + "\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(15.0)
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/agents")
@router.get("/api/agents/")
async def get_agents_list() -> list[dict[str, object]]:
    profiles = await _safe_list_bot_profiles()
    if not profiles:
        return []

    rows: list[dict[str, object]] = []
    for bot_id, profile in profiles.items():
        lifecycle = str(profile.get("lifecycle_state", "unknown"))
        enabled = bool(profile.get("enabled", False))
        status = "active" if enabled and lifecycle not in {"retired", "offline"} else "idle"
        rows.append(
            {
                "name": bot_id,
                "role": str(profile.get("strategy_type", profile.get("mission", "trading_unit"))),
                "schedule": "on_demand",
                "status": status,
                "lifecycle_state": lifecycle,
            }
        )
    rows.sort(key=lambda item: str(item["name"]))
    return rows


@router.get("/agents")
async def get_agents_alias() -> RedirectResponse:
    return RedirectResponse(url="/api/agents")


@router.get("/api/news")
async def get_news(limit: int = 30) -> dict[str, object]:
    safe_limit = max(1, min(limit, 100))
    news_rows = await ScanService().list_news_articles(limit=safe_limit)
    return {"news": news_rows[:safe_limit], "count": min(safe_limit, len(news_rows))}


@router.get("/api/news/latest")
async def get_news_latest() -> dict[str, object]:
    payload = await get_news(limit=20)
    return {"articles": payload["news"], "count": payload["count"]}


@router.get("/api/signals")
async def get_signals(limit: int = 50, container: Container = Depends(get_container)) -> dict[str, object]:
    safe_limit = max(1, min(limit, 500))
    query_error: str | None = None
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            db_signals = await repo.list_recent(limit=max(safe_limit, 200))
        signals = [
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
                "blocked_reason": signal.blocked_reason,
                "metadata": signal.metadata,
                "created_at": signal.created_at.isoformat(),
            }
            for signal in db_signals
        ]
    except Exception as exc:
        signals = []
        query_error = f"{type(exc).__name__}: {exc}"
        logger.exception("api.signals query failed")
    today = datetime.now(timezone.utc).date()

    pending = 0
    rejected = 0
    today_count = 0
    rows: list[dict[str, object]] = []
    for signal in signals:
        status = str(signal.get("status", "pending"))
        if status == "pending":
            pending += 1
        if status == "rejected":
            rejected += 1
        created_at_raw = signal.get("created_at")
        created_at = _parse_datetime(created_at_raw)
        if created_at is not None and created_at.date() == today:
            today_count += 1
        rows.append(
            {
                "signal_id": signal.get("signal_id"),
                "ticker": signal.get("ticker") or signal.get("symbol"),
                "symbol": signal.get("symbol"),
                "headline": signal.get("headline"),
                "action": signal.get("action"),
                "status": status,
                "score": signal.get("score"),
                "conf": signal.get("conf", signal.get("confidence")),
                "confidence": signal.get("confidence", signal.get("conf")),
                "source": signal.get("source"),
                "lane_hint": signal.get("lane_hint"),
                "strategy_hint": signal.get("strategy_hint"),
                "schema_version": signal.get("schema_version", 1),
                "blocked_reason": signal.get("blocked_reason"),
                "reason_code": _signal_reason_code(signal),
                "reason": _humanize_signal_reason(_signal_reason_code(signal)),
                "created_at": created_at.isoformat() if created_at is not None else created_at_raw,
            }
        )

    return {
        "signals": rows[:safe_limit],
        "count": len(rows),
        "total": len(rows),
        "pending": pending,
        "rejected": rejected,
        "today": today_count,
        "truth_source": "signals_db",
        "degraded": query_error is not None or not container.db_context_ready,
        "db_context_ready": container.db_context_ready,
        "query_error": query_error,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/performance")
async def get_performance() -> dict[str, object]:
    trades = await _safe_recent_trades(limit=500)
    total = len(trades)
    wins = 0
    losses = 0
    pnl_values: list[float] = []
    for trade in trades:
        outcome = trade.outcome.value
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        if trade.pnl_pct is not None:
            pnl_values.append(float(trade.pnl_pct))

    avg_pnl_pct = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
    win_rate = (wins / total) if total > 0 else 0.0
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/alerts/status")
async def get_alerts_status() -> dict[str, object]:
    recent_alerts = await _safe_recent_outbox(limit=50)
    alert_events = [
        row for row in recent_alerts if str(row.get("event_type", "")).lower().startswith("alert")
    ]
    return {
        "enabled": False,
        "provider": "none",
        "status": "unconfigured",
        "recent_alert_events": len(alert_events),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/scanner")
async def get_scanner(container: Container = Depends(get_container)) -> dict[str, object]:
    stocks: list[dict[str, object]] = []
    for symbol in WATCHLIST_SYMBOLS:
        quote = await _safe_broker_quote(container, symbol)
        stocks.append(
            {
                "symbol": symbol,
                "last": _as_float(quote.get("last")),
                "bid": _as_float(quote.get("bid")),
                "ask": _as_float(quote.get("ask")),
            }
        )
    return {
        "stocks": stocks,
        "count": len(stocks),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/scanner/{symbol}")
async def get_scanner_symbol(symbol: str, container: Container = Depends(get_container)) -> dict[str, object]:
    quote = await _safe_broker_quote(container, symbol.upper())
    return {
        "symbol": symbol.upper(),
        "last": _as_float(quote.get("last")),
        "bid": _as_float(quote.get("bid")),
        "ask": _as_float(quote.get("ask")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/news/scan")
async def trigger_news_scan(container: Container = Depends(get_container)) -> dict[str, object]:
    return await ScanService().run_news_scan(container, origin="api.news_scan")


@router.post("/api/alerts/test")
async def test_alert() -> dict[str, object]:
    triggered_at = datetime.now(timezone.utc).isoformat()
    try:
        async with UnitOfWork() as uow:
            await append_outbox_event(
                event_type="AlertTestRequested",
                entity_key=f"alert-test:{triggered_at}",
                payload={"triggered_at": triggered_at, "source": "api.alerts.test"},
                connection=uow.connection,
            )
    except Exception:
        return {"sent": False, "reason": "db_unavailable"}
    return {"sent": True, "triggered_at": triggered_at}


@router.get("/api/data-fabric/status")
async def get_data_fabric_status(container: Container = Depends(get_container)) -> dict[str, object]:
    scout_status_payload = await ScanService().get_scout_status()
    news_rows = await ScanService().list_news_articles(limit=20)
    return {
        "scanner": {"enabled": True, "watchlist_size": len(WATCHLIST_SYMBOLS)},
        "fabric": {"runtime_mode": "asyncio", "scout_last_scan": scout_status_payload.get("last_scan")},
        "news": {"recent_articles": len(news_rows)},
        "queue": container.queue_bus.snapshot_sizes(),
    }


@router.get("/api/scout/status")
async def scout_status(container: Container = Depends(get_container)) -> dict[str, object]:
    payload = await ScanService().get_scout_status()
    payload["status"] = "ready"
    payload["queue"] = container.queue_bus.snapshot_sizes()
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.post("/api/scout/scan")
async def scout_scan(container: Container = Depends(get_container)) -> dict[str, object]:
    return await ScanService().run_scout_scan(container, origin="api.scout_scan")


@router.get("/api/scout/items")
async def scout_items(source: str | None = None, limit: int = 50) -> dict[str, object]:
    safe_limit = max(1, min(limit, 200))
    items = await ScanService().list_scout_items(source=source, limit=safe_limit)
    return {"items": items, "count": len(items)}


@router.get("/api/backtest")
async def run_backtest(symbol: str = "AAPL", strategy: str = "default", period: str = "6mo") -> dict[str, object]:
    outcomes = await _safe_recent_trades(limit=1000)
    selected = [item for item in outcomes if item.symbol.upper() == symbol.upper()]
    if not selected:
        return {"symbol": symbol.upper(), "strategy": strategy, "period": period, "trades": 0, "note": "no_trade_data"}

    wins = sum(1 for item in selected if item.outcome.value == "win")
    losses = sum(1 for item in selected if item.outcome.value == "loss")
    pnl_values = [float(item.pnl_pct) for item in selected if item.pnl_pct is not None]
    avg_pnl = (sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0
    return {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "period": period,
        "trades": len(selected),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(selected), 4),
        "avg_pnl_pct": round(avg_pnl, 4),
    }


@router.get("/api/backtest/all")
async def run_all_backtests() -> dict[str, object]:
    outcomes = await _safe_recent_trades(limit=1500)
    by_symbol: dict[str, list[float]] = {}
    wins_by_symbol: dict[str, int] = {}
    total_by_symbol: dict[str, int] = {}
    for item in outcomes:
        symbol = item.symbol.upper()
        by_symbol.setdefault(symbol, [])
        total_by_symbol[symbol] = total_by_symbol.get(symbol, 0) + 1
        if item.pnl_pct is not None:
            by_symbol[symbol].append(float(item.pnl_pct))
        if item.outcome.value == "win":
            wins_by_symbol[symbol] = wins_by_symbol.get(symbol, 0) + 1

    report: list[dict[str, object]] = []
    for symbol in sorted(total_by_symbol.keys()):
        total = total_by_symbol[symbol]
        wins = wins_by_symbol.get(symbol, 0)
        pnls = by_symbol.get(symbol, [])
        avg_pnl = (sum(pnls) / len(pnls)) if pnls else 0.0
        report.append(
            {
                "symbol": symbol,
                "trades": total,
                "wins": wins,
                "win_rate": round(wins / total, 4) if total > 0 else 0.0,
                "avg_pnl_pct": round(avg_pnl, 4),
            }
        )
    return {"symbols": report, "count": len(report)}


@router.get("/api/trading-truth")
async def trading_truth() -> dict[str, object]:
    perf = await get_performance()
    return {
        "total_closed_trades": perf["total_trades"],
        "fleet_win_rate": perf["win_rate"],
        "avg_pnl_pct": perf["avg_pnl_pct"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/quantstats/summary")
async def quantstats_summary() -> dict[str, object]:
    perf = await get_performance()
    wins = _as_int(perf.get("wins"))
    losses = _as_int(perf.get("losses"))
    win_rate = _as_float(perf.get("win_rate"))
    avg_return_pct = _as_float(perf.get("avg_pnl_pct"))
    expectancy = round((win_rate * avg_return_pct) - ((1.0 - win_rate) * abs(avg_return_pct)), 4)
    profit_factor = round((wins / losses), 4) if losses > 0 else float(wins if wins > 0 else 0.0)
    return {
        "trades": perf["total_trades"],
        "win_rate": perf["win_rate"],
        "avg_return_pct": perf["avg_pnl_pct"],
        "expectancy_pct": expectancy,
        "profit_factor": profit_factor,
    }


@router.get("/api/scout/theses")
async def scout_theses() -> dict[str, object]:
    theses = await ScanService().list_scout_theses(limit=25)
    status_payload = await ScanService().get_scout_status()
    return {"theses": theses, "count": len(theses), "last_scan": status_payload.get("last_scan")}


async def _safe_recent_outbox(limit: int) -> list[dict[str, object]]:
    try:
        async with UnitOfWork() as uow:
            repo = OutboxRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_recent_signals(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_recent_trades(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_list_bot_profiles() -> dict[str, dict]:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.list_bot_profiles()
    except Exception:
        return {}


async def _safe_broker_quote(container: Container, symbol: str) -> dict[str, object]:
    try:
        quote = await container.broker.get_quote(symbol)
        return quote if isinstance(quote, dict) else {}
    except Exception:
        return {}


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _humanize_signal_reason(reason: object) -> str | None:
    raw = str(reason or "").strip()
    if not raw:
        return None
    normalized = raw.lower()
    mapped = SIGNAL_REASON_LABELS.get(normalized)
    if mapped is not None:
        return mapped
    if normalized.startswith("stale_signal:"):
        return "Rejected because the signal was too old when execution was attempted."
    if normalized.startswith("signal_price_drift:"):
        return "Rejected because price drift exceeded the allowed execution threshold."
    if normalized.startswith("pdt_block:"):
        return "Rejected by pattern day trader protection rules."
    if normalized.startswith("insufficient_buying_power:"):
        return "Execution was blocked because the broker account does not have enough buying power."
    if normalized.startswith("insufficient_cash:"):
        return "Execution was blocked because the account cash balance is too low for the order."
    if normalized.startswith("consecutive_losses="):
        return "Rejected because consecutive loss protection is active."
    return raw.replace("_", " ").strip().capitalize() + "."


def _signal_reason_code(signal: dict[str, object]) -> str | None:
    status = str(signal.get("status") or "").strip().lower()
    metadata = signal.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    approval_reason = _reason_text(metadata_dict.get("approval_reason"))
    failure_reason = _reason_text(metadata_dict.get("failure_reason"))
    blocked_reason = _reason_text(signal.get("blocked_reason"))

    if status in {"failed", "rejected"}:
        reason = failure_reason or blocked_reason
        if reason == "bypass_council":
            return "execution_failed" if status == "failed" else "execution_rejected"
        return reason
    if status in {"approved", "executed"}:
        return approval_reason
    return blocked_reason or approval_reason or failure_reason


def _reason_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
