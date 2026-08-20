from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from api.deps import get_container
from app.services.control_plane.service import RuntimeControlPlaneService
from app.services.intelligence.service import IntelligenceService
from app.services.order_status.reconcile import OrderStatusReconcileService
from app.services.outcome.service import OutcomeLifecycleService
from app.services.portfolio.service import PortfolioService
from app.services.signals.service import SignalService
from app.services.reconcile.service import ReconcileService
from app.usecases.process_pending_signals import process_pending_signals
from app.usecases.submit_order import submit_order
from db.repositories.positions_repo import PositionSnapshotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import OrderAction, SignalStatus
from domain.models.signal import Signal
from runtime.container import Container

router = APIRouter(prefix="/api", tags=["api-compat"])

WATCHLIST_SYMBOLS: tuple[str, ...] = (
    "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD", "INTC", "JPM",
    "BAC", "XOM", "CVX", "GS", "V", "MA", "UNH", "JNJ", "GDX", "GDXJ",
    "COPX", "LIT", "MU", "COIN", "HOOD", "NFLX", "DIS", "BA", "PFE", "SPY",
)

_ROUTE_CACHE_TTL_SECONDS = 5.0
_route_cache: dict[str, tuple[float, object]] = {}


class HaltRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    action: OrderAction
    quantity: float
    score: float = 0.9
    signal_id: str | None = None


@router.get("/health")
async def api_health(container: Container = Depends(get_container)) -> dict[str, object]:
    runtime = await RuntimeControlPlaneService().snapshot(container)
    return {
        "status": "ok",
        "runtime_mode": "asyncio",
        "db_driver": "tortoise",
        "broker_mode": container.broker_router.default_broker_name,
        "runtime": runtime,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/health")
async def api_system_health() -> RedirectResponse:
    return RedirectResponse(url="/api/health")


@router.get("/account")
async def api_account(container: Container = Depends(get_container)) -> dict[str, object]:
    account = await _safe_broker_account(container.broker)
    equity = _as_float(account.get("equity"))
    last_equity = _as_float(account.get("last_equity"))
    return {
        "equity": equity,
        "cash": _as_float(account.get("cash")),
        "buying_power": _as_float(account.get("buying_power")),
        "pnl_today": equity - last_equity,
        "last_equity": last_equity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/positions")
async def api_positions(container: Container = Depends(get_container)) -> dict[str, object]:
    snapshot_payload = await _safe_latest_portfolio_snapshot_payload()

    positions = (
        snapshot_payload.get("positions", [])
        if isinstance(snapshot_payload, dict)
        else []
    )
    if not isinstance(positions, list):
        positions = []
    normalized: list[dict[str, object]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol", "")).upper()
        qty = _as_float(position.get("quantity"))
        entry_price = _as_float(position.get("avg_entry_price"))
        current_price = _as_float(position.get("current_price"))
        market_value = _as_float(position.get("market_value"))
        if market_value == 0.0 and current_price > 0.0:
            market_value = abs(qty) * current_price
        normalized.append(
            {
                "symbol": symbol,
                "qty": qty,
                "side": "long" if qty >= 0 else "short",
                "entry_price": entry_price,
                "avg_entry_price": entry_price,
                "current_price": current_price,
                "market_value": round(market_value, 4),
                "unrealized_pl": _as_float(position.get("unrealized_pl")),
                "unrealized_plpc": _as_float(position.get("unrealized_plpc")),
                "broker": str(position.get("broker", "paper")),
                "currency": str(position.get("currency", "USD")),
            }
        )
    return {"positions": normalized, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/portfolio/allocation")
async def api_portfolio_allocation(container: Container = Depends(get_container)) -> dict[str, object]:
    service = PortfolioService(broker=container.broker)
    return await service.allocation_summary()


@router.get("/portfolio/equity")
async def api_portfolio_equity(container: Container = Depends(get_container)) -> dict[str, float]:
    account = await _safe_broker_account(container.broker)
    return {"equity": _as_float(account.get("equity"))}


@router.get("/trades")
async def api_trades(limit: int = 100) -> list[dict[str, object]]:
    safe_limit = max(1, min(limit, 500))
    cache_key = f"trades:{safe_limit}"
    cached = _cache_get(cache_key)
    if isinstance(cached, list):
        return cached
    try:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            outcomes = await repo.list_recent_db_truth(limit=safe_limit)
            snapshots_repo = PositionSnapshotsRepository(connection=uow.connection)
            snapshots = await _load_trade_snapshots_for_backfill(
                rows=[_trade_payload(item) for item in outcomes],
                snapshots_repo=snapshots_repo,
            )
    except Exception:
        outcomes = []
        snapshots = []

    rows = _dedupe_trade_rows([_trade_payload(item) for item in outcomes])
    rows = _backfill_trade_excursions(rows=rows, snapshots=snapshots)
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    result = rows[:safe_limit]
    _cache_set(cache_key, result)
    return result


@router.get("/trades/today")
async def api_trades_today() -> list[dict[str, object]]:
    today = datetime.now(timezone.utc).date()
    rows = await api_trades(limit=200)
    result: list[dict[str, object]] = []
    for row in rows:
        closed_at = row.get("closed_at")
        if not isinstance(closed_at, str):
            continue
        try:
            dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.date() == today:
            result.append(row)
    return result


@router.get("/executor/status")
async def api_executor_status(container: Container = Depends(get_container)) -> dict[str, object]:
    pending = 0
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            pending = await repo.count_pending()
    except Exception:
        pending = 0

    return {
        "status": "running" if container.process_manager is not None else "idle",
        "trades_today": len(await api_trades_today()),
        "signals_pending": pending,
        "halted": container.trading_halted,
        "queue": container.queue_bus.snapshot_sizes(),
    }


@router.get("/watchlist")
async def api_watchlist(container: Container = Depends(get_container)) -> list[dict[str, object]]:
    held_symbols: set[str] = set()
    try:
        positions = await container.broker.get_positions()
        for row in positions:
            symbol = str(row.get("symbol", "")).upper()
            if symbol:
                held_symbols.add(symbol)
    except Exception:
        held_symbols = set()
    return [
        {"symbol": symbol, "held": symbol in held_symbols}
        for symbol in WATCHLIST_SYMBOLS
    ]


@router.post("/executor/run")
async def api_executor_run(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.trading_halted:
        return {"triggered": False, "error": "trading halted", "reason": container.trading_halt_reason}
    result = await process_pending_signals(limit=100, quantity=1.0, process_manager=container.process_manager)
    return {"triggered": True, "result": result}


@router.post("/signals/reprocess")
async def api_signals_reprocess(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.trading_halted:
        return {"reprocessed": False, "error": "trading halted", "reason": container.trading_halt_reason}
    result = await process_pending_signals(limit=100, quantity=1.0, process_manager=container.process_manager)
    return {"reprocessed": True, "result": result}


@router.get("/brokers/status")
async def api_brokers_status(container: Container = Depends(get_container)) -> dict[str, object]:
    cached = _cache_get("brokers:status")
    if isinstance(cached, dict):
        return cached
    broker_names = list(container.broker_router.list_brokers())
    rows = await asyncio.gather(
        *[_broker_status_row(name=name, broker=container.broker_router.get(name)) for name in broker_names]
    )
    payload = {
        "brokers": rows,
        "count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set("brokers:status", payload)
    return payload


@router.get("/brokers/{broker_name}/account")
async def api_broker_account(
    broker_name: str,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    broker = container.broker_router.get(broker_name)
    if broker is None:
        return {"error": f"Broker '{broker_name}' not found"}
    account = await _safe_broker_account(broker)
    return {
        "broker": broker_name,
        "connected": bool(account.get("connected", not bool(account.get("error")))),
        **account,
    }


@router.get("/recon/status")
async def api_recon_status(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.last_reconcile_report is None:
        return {
            "status": "NEVER_RUN",
            "last_run": None,
            "has_report": False,
            "note": "Reconciliation runs on-demand or via scheduler",
        }
    issues = container.last_reconcile_report.get("issues", [])
    issue_count = len(issues) if isinstance(issues, list) else 0
    return {
        "status": "OK" if issue_count == 0 else "WARN",
        "last_run": container.last_reconcile_run_at,
        "has_report": True,
        "issues": issue_count,
    }


@router.get("/recon/report")
async def api_recon_report(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.last_reconcile_report is None:
        return {"error": "No report available - run /api/recon/run first"}
    return container.last_reconcile_report


@router.post("/recon/run")
async def api_recon_run(container: Container = Depends(get_container)) -> dict[str, object]:
    truth_sync = await _sync_trading_truth(container)
    service = ReconcileService(broker=container.broker)
    try:
        report = await service.run()
    except Exception as exc:
        return {"error": str(exc)[:200], "status": "failed"}
    container.last_reconcile_report = report
    timestamp = report.get("timestamp")
    if isinstance(timestamp, str):
        container.last_reconcile_run_at = timestamp
    report["truth_sync"] = truth_sync
    return report


@router.post("/runtime/sync/trading-truth")
async def api_runtime_sync_trading_truth(container: Container = Depends(get_container)) -> dict[str, object]:
    payload = await _sync_trading_truth(container)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.get("/risk")
async def api_risk(container: Container = Depends(get_container)) -> dict[str, object]:
    return await api_risk_status(container)


@router.get("/risk/status")
async def api_risk_status(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().risk_status(container)


@router.post("/emergency/halt")
async def api_emergency_halt(
    body: HaltRequest | None = None,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    container.trading_halted = True
    container.trading_halt_reason = body.reason if body is not None else None
    container.trading_halted_at = datetime.now(timezone.utc).isoformat()
    return {
        "halted": True,
        "reason": container.trading_halt_reason,
        "halted_at": container.trading_halted_at,
    }


@router.post("/emergency/resume")
async def api_emergency_resume(container: Container = Depends(get_container)) -> dict[str, object]:
    previous_reason = container.trading_halt_reason
    container.trading_halted = False
    container.trading_halt_reason = None
    container.trading_halted_at = None
    return {"halted": False, "resumed": True, "previous_reason": previous_reason}


@router.get("/monitor/status")
async def api_monitor_status(container: Container = Depends(get_container)) -> dict[str, object]:
    runtime = await RuntimeControlPlaneService().snapshot(container)
    return {
        "status": "HALTED" if container.trading_halted else str(runtime["status"]).upper(),
        "trading_halted": container.trading_halted,
        "halt_reason": container.trading_halt_reason,
        "grade": runtime["grade"],
        "alerts": runtime["alerts"],
        "duties": runtime["duties"],
        "queue": container.queue_bus.snapshot_sizes(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "is_stale": bool(runtime["reconcile"].get("stale", False)),
    }


@router.get("/monitor/summary")
async def api_monitor_summary(container: Container = Depends(get_container)) -> dict[str, object]:
    from api.routers.control_plane_compat import build_monitor_summary_payload

    cached = _cache_get("monitor:summary")
    if isinstance(cached, dict):
        return cached
    summary = await build_monitor_summary_payload(container)
    summary["status"] = "HALTED" if container.trading_halted else "OK"
    _cache_set("monitor:summary", summary)
    return summary


@router.get("/ibkr/status")
async def api_ibkr_status(container: Container = Depends(get_container)) -> dict[str, object]:
    ibkr = container.broker_router.get("ibkr")
    if ibkr is None:
        return {"enabled": False, "status": "UNAVAILABLE", "broker": "Interactive Brokers"}
    try:
        account = await ibkr.get_account()
    except Exception as exc:
        return {
            "enabled": True,
            "status": "ERROR",
            "broker": "Interactive Brokers",
            "error": str(exc)[:200],
        }
    return {
        "enabled": True,
        "status": "CONNECTED",
        "broker": "Interactive Brokers",
        "currency": account.get("currency", "USD"),
        "equity": _as_float(account.get("equity")),
        "cash": _as_float(account.get("cash")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/ibkr/reconnect")
async def api_ibkr_reconnect(container: Container = Depends(get_container)) -> dict[str, object]:
    ibkr = container.broker_router.get("ibkr")
    if ibkr is None:
        return {"reconnected": False, "error": "No IBKR adapter"}
    try:
        await ibkr.get_account()
    except Exception as exc:
        return {"reconnected": False, "error": str(exc)[:200]}
    return {"reconnected": True}


@router.post("/execute")
async def api_execute(
    body: ExecuteRequest | None = None,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    if body is None:
        return {
            "executed": False,
            "status": "noop",
            "error": "missing execute payload",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    if container.trading_halted:
        return {"executed": False, "error": "trading halted", "reason": container.trading_halt_reason}
    if body.quantity <= 0:
        return {"executed": False, "error": "quantity must be > 0"}

    signal_id = body.signal_id or f"manual:{body.symbol.upper()}:{uuid4().hex[:12]}"
    signal = Signal(
        signal_id=signal_id,
        symbol=body.symbol.upper(),
        action=body.action,
        score=body.score,
        confidence=body.score,
        source="manual",
        lane_hint="manual",
        strategy_hint="manual_order",
        headline=f"Manual {body.action.value} order for {body.symbol.upper()}",
        status=SignalStatus.PENDING,
    )

    await SignalService().ingest_signal(signal)
    execution = await submit_order(
        signal,
        quantity=body.quantity,
        broker_router=container.broker_router,
    )
    if execution is None:
        return {
            "executed": False,
            "signal_id": signal_id,
            "status": "rejected",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "executed": True,
        "signal_id": signal_id,
        "order_id": execution.order_id,
        "broker_order_id": execution.broker_order_id,
        "status": execution.status.value,
        "filled_qty": execution.filled_qty,
        "avg_price": execution.avg_price,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _safe_broker_account(broker: object) -> dict[str, object]:
    get_account = getattr(broker, "get_account", None)
    if not callable(get_account):
        return {}
    try:
        account = await get_account()
        return account if isinstance(account, dict) else {}
    except Exception:
        return {}


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0


def _trade_payload(item: object) -> dict[str, object]:
    raw_features = getattr(item, "features", None)
    features = raw_features if isinstance(raw_features, dict) else {}
    raw_action = getattr(item, "action", None)
    quantity = getattr(item, "quantity", None)
    entry_price = getattr(item, "entry_price", None)
    exit_price = getattr(item, "exit_price", None)
    item_outcome = getattr(item, "outcome")
    outcome_value = item_outcome.value if hasattr(item_outcome, "value") else str(item_outcome)
    opened_at = getattr(item, "opened_at", None)
    closed_at = getattr(item, "closed_at", None)
    source = getattr(item, "source", None)
    signal_id = getattr(item, "signal_id", None)
    close_reason = getattr(item, "close_reason", None)
    bot_id = getattr(item, "bot_id", None)
    broker_name = getattr(item, "broker_name", None)
    market = getattr(item, "market", None)
    order_type = getattr(item, "order_type", None)
    raw_pnl_pct = getattr(item, "pnl_pct", None)
    mfe_pct = _pick_float(features, "mfe_pct")
    mae_pct = _pick_float(features, "mae_pct")
    action_text = None if raw_action is None else str(raw_action).upper()
    return {
        "trade_id": getattr(item, "trade_id"),
        "signal_id": signal_id,
        "symbol": getattr(item, "symbol"),
        "action": action_text,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "qty": quantity,
        "pnl_pct": raw_pnl_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "outcome": str(outcome_value).upper(),
        "close_reason": close_reason,
        "bot_id": bot_id,
        "source": source,
        "broker_name": broker_name,
        "market": market,
        "order_type": order_type,
        "features": features,
        "opened_at": opened_at.isoformat() if opened_at is not None else None,
        "created_at": opened_at.isoformat() if opened_at is not None else None,
        "closed_at": closed_at.isoformat() if closed_at is not None else None,
    }


def _backfill_trade_excursions(
    *,
    rows: list[dict[str, object]],
    snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not rows:
        return rows
    price_index = _build_snapshot_price_index(snapshots)
    if not price_index:
        return [_apply_point_in_time_excursion_fallback(row) for row in rows]

    return [
        _fill_trade_excursions_from_snapshots(row=row, price_index=price_index)
        for row in rows
    ]


async def _load_trade_snapshots_for_backfill(
    *,
    rows: list[dict[str, object]],
    snapshots_repo: PositionSnapshotsRepository,
) -> list[dict[str, object]]:
    candidates = [
        row for row in rows
        if row.get("mfe_pct") is None or row.get("mae_pct") is None
    ]
    if not candidates:
        return []

    snapshot_rows = [
        row
        for row in candidates[:40]
        if str(row.get("outcome") or "").upper() == "OPEN"
    ]
    if not snapshot_rows:
        return []

    starts = [
        _parse_iso_datetime(row.get("opened_at") or row.get("created_at"))
        for row in snapshot_rows
    ]
    valid_starts = [value for value in starts if value is not None]
    if not valid_starts:
        return []
    cutoff = max(
        min(valid_starts) - timedelta(minutes=2),
        datetime.now(timezone.utc) - timedelta(hours=6),
    )
    return await snapshots_repo.list_since(cutoff=cutoff, limit=5000)


def _fill_trade_excursions_from_snapshots(
    *,
    row: dict[str, object],
    price_index: dict[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, object]:
    if row.get("mfe_pct") is not None and row.get("mae_pct") is not None:
        return row

    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return _apply_point_in_time_excursion_fallback(row)

    start = _parse_iso_datetime(row.get("opened_at") or row.get("created_at"))
    if start is None:
        return _apply_point_in_time_excursion_fallback(row)
    end = _parse_iso_datetime(row.get("closed_at")) or datetime.now(timezone.utc)
    if end < start:
        end = start

    broker_name = str(row.get("broker_name") or "").strip().lower()
    action = str(row.get("action") or "BUY").strip().upper()
    entry_price = _pick_float(row, "entry_price")
    if entry_price is None or entry_price <= 0:
        return row

    series: list[float] = []
    broker_keys = [(symbol, broker_name)] if broker_name else []
    broker_keys.append((symbol, ""))
    seen_points: set[tuple[datetime, float]] = set()
    for key in broker_keys:
        for observed_at, price in price_index.get(key, []):
            if observed_at < start - timedelta(seconds=30) or observed_at > end + timedelta(seconds=30):
                continue
            point = (observed_at, price)
            if point in seen_points:
                continue
            seen_points.add(point)
            series.append(price)
    exit_price = _pick_float(row, "exit_price")
    if exit_price is not None and exit_price > 0:
        series.append(exit_price)

    if not series:
        return _apply_point_in_time_excursion_fallback(row)

    pnl_series = [_price_to_pnl_pct(entry_price=entry_price, observed_price=price, action=action) for price in series]
    favorable = max([0.0, *pnl_series])
    adverse = min([0.0, *pnl_series])

    updated = dict(row)
    updated["mfe_pct"] = round(favorable, 4)
    updated["mae_pct"] = round(adverse, 4)
    return updated


def _apply_point_in_time_excursion_fallback(row: dict[str, object]) -> dict[str, object]:
    if row.get("mfe_pct") is not None and row.get("mae_pct") is not None:
        return row
    entry_price = _pick_float(row, "entry_price")
    if entry_price is None or entry_price <= 0:
        return row
    observed = _pick_float(row, "exit_price")
    if observed is None or observed <= 0:
        pnl_pct = _pick_float(row, "pnl_pct")
        if pnl_pct is None:
            return row
        favorable = max(0.0, pnl_pct)
        adverse = min(0.0, pnl_pct)
    else:
        pnl_pct = _price_to_pnl_pct(
            entry_price=entry_price,
            observed_price=observed,
            action=str(row.get("action") or "BUY").strip().upper(),
        )
        favorable = max(0.0, pnl_pct)
        adverse = min(0.0, pnl_pct)
    updated = dict(row)
    updated["mfe_pct"] = round(favorable, 4)
    updated["mae_pct"] = round(adverse, 4)
    return updated


def _build_snapshot_price_index(
    snapshots: list[dict[str, object]],
) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    index: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for snapshot in snapshots:
        created_at = _parse_iso_datetime(snapshot.get("created_at"))
        payload = snapshot.get("payload")
        if created_at is None or not isinstance(payload, dict):
            continue
        positions = payload.get("positions")
        if not isinstance(positions, list):
            continue
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or "").upper().strip()
            price = _pick_float(position, "current_price")
            if not symbol or price is None or price <= 0:
                continue
            broker = str(position.get("broker") or "").strip().lower()
            index.setdefault((symbol, broker), []).append((created_at, price))
            if broker:
                index.setdefault((symbol, ""), []).append((created_at, price))
    for values in index.values():
        values.sort(key=lambda item: item[0])
    return index


async def _broker_status_row(*, name: str, broker: object | None) -> dict[str, object]:
    if broker is None:
        return {
            "name": name,
            "connected": False,
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "last_equity": 0.0,
            "portfolio_value": 0.0,
            "currency": "USD",
            "mode": "paper",
            "status": "unknown",
            "account_number": "",
            "error": "broker_not_found",
        }
    try:
        account = await cast(Any, broker).get_account()
        connected = bool(account.get("connected", not bool(account.get("error"))))
    except Exception:
        account = {}
        connected = False
    return {
        "name": name,
        "connected": connected,
        "equity": _as_float(account.get("equity")),
        "cash": _as_float(account.get("cash")),
        "buying_power": _as_float(account.get("buying_power")),
        "last_equity": _as_float(account.get("last_equity")),
        "portfolio_value": _as_float(account.get("portfolio_value")),
        "currency": str(account.get("currency", "USD")),
        "mode": str(account.get("mode", "paper")),
        "status": str(account.get("status", "unknown")),
        "account_number": str(account.get("account_number") or account.get("account") or ""),
        "error": str(account.get("error", "")),
    }


def _price_to_pnl_pct(*, entry_price: float, observed_price: float, action: str) -> float:
    if entry_price <= 0:
        return 0.0
    if action == OrderAction.SELL.value.upper():
        return ((entry_price - observed_price) / entry_price) * 100.0
    return ((observed_price - entry_price) / entry_price) * 100.0


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dedupe_trade_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    ordered_keys: list[str] = []
    for row in rows:
        key = _trade_identity_key(row)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = row
            ordered_keys.append(key)
            continue
        if _prefer_trade_row(candidate=row, incumbent=existing):
            grouped[key] = row
    return [grouped[key] for key in ordered_keys]


def _trade_identity_key(row: dict[str, object]) -> str:
    features = row.get("features")
    feature_dict = features if isinstance(features, dict) else {}
    broker_order_id = row.get("broker_order_id") or feature_dict.get("broker_order_id")
    if isinstance(broker_order_id, str) and broker_order_id.strip():
        return f"broker:{broker_order_id.strip()}"
    trade_id = row.get("trade_id")
    if isinstance(trade_id, str) and trade_id.strip():
        return f"trade:{trade_id.strip()}"
    signal_id = row.get("signal_id")
    if isinstance(signal_id, str) and signal_id.strip():
        return f"signal:{signal_id.strip()}"
    symbol = str(row.get("symbol") or "").strip().upper()
    created_at = str(row.get("created_at") or "").strip()
    return f"fallback:{symbol}:{created_at}"


def _prefer_trade_row(*, candidate: dict[str, object], incumbent: dict[str, object]) -> bool:
    return _trade_row_priority(candidate) > _trade_row_priority(incumbent)


def _trade_row_priority(row: dict[str, object]) -> tuple[int, int, int]:
    bot_id = str(row.get("bot_id") or "").strip().lower()
    source = str(row.get("source") or "").strip().lower()
    features = row.get("features")
    feature_dict = features if isinstance(features, dict) else {}
    has_known_bot = int(bool(bot_id) and bot_id != "unknown")
    is_non_sync = int(source != "alpaca_sync")
    has_direct_order_link = int(bool(row.get("trade_id")) and str(row.get("trade_id")).isdigit())
    has_feature_signal = int(isinstance(feature_dict.get("signal_id"), str))
    return (has_known_bot, is_non_sync, has_direct_order_link + has_feature_signal)


async def _safe_latest_portfolio_snapshot_payload() -> dict[str, object] | None:
    try:
        async with UnitOfWork() as uow:
            rows = await PositionSnapshotsRepository(connection=uow.connection).list_recent(limit=1)
    except Exception:
        return None
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, dict):
        return payload
    return None


async def _sync_trading_truth(container: Container) -> dict[str, object]:
    portfolio_payload: dict[str, object] = {}
    try:
        portfolio_payload = await PortfolioService(broker=container.broker).snapshot()
    except Exception as exc:
        portfolio_payload = {"error": str(exc)[:200]}

    try:
        order_sync = await OrderStatusReconcileService(container.broker_router).reconcile_active_orders(limit=200)
    except Exception as exc:
        order_sync = {"error": str(exc)[:200], "checked": 0, "updated": 0, "filled": 0, "failed": 0, "skipped": 0}
    try:
        lifecycle = await OutcomeLifecycleService(broker=container.broker).reconcile_open_outcomes()
    except Exception as exc:
        lifecycle = {
            "error": str(exc)[:200],
            "checked": 0,
            "closed_count": 0,
            "orphan_open": [],
            "quotes_checked": 0,
        }
    return {
        "portfolio_snapshot": {
            "position_count": portfolio_payload.get("position_count", 0)
            if isinstance(portfolio_payload, dict)
            else 0,
            "source": portfolio_payload.get("source")
            if isinstance(portfolio_payload, dict)
            else "error",
            "error": portfolio_payload.get("error") if isinstance(portfolio_payload, dict) else None,
        },
        "order_status_sync": order_sync,
        "outcome_lifecycle_sync": lifecycle,
    }


def _pick_float(features: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = features.get(key)
        if value is None:
            continue
        try:
            return float(cast(Any, value))
        except (TypeError, ValueError):
            continue
    return None


def _cache_get(key: str) -> object | None:
    cached = _route_cache.get(key)
    if cached is None:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _route_cache.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: object, *, ttl_seconds: float = _ROUTE_CACHE_TTL_SECONDS) -> None:
    _route_cache[key] = (time.monotonic() + ttl_seconds, payload)
