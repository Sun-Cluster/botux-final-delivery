from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_container
from api.routers.compat.api_extra_utils import (
    as_float as _as_float,
    as_int as _as_int,
    execution_order_id as _execution_order_id,
    json_payload as _json_payload,
    merge_json as _merge_json,
    order_signal_id as _order_signal_id,
)
from app.services.governance.service import GovernanceService
from app.services.intelligence.service import IntelligenceService
from app.services.market.data import MarketDataService
from app.services.measurement.service import MeasurementService, _ScorecardSummary
from app.services.registry.seeder import CANONICAL_BOT_PROFILES, CANONICAL_STRATEGIES
from app.services.scan.service import ScanService
from app.services.signals.service import SignalService
from app.usecases.submit_order import submit_order
from db.models import AuditLog, ExecutionRecord, OrderRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.positions_repo import PositionSnapshotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import OrderAction, SignalStatus
from domain.models.signal import Signal
from runtime.container import Container

router = APIRouter(tags=["api-extra-compat"])
MEMORY_TRADING_CHANGE_CHECKLISTS: list[dict[str, object]] = []

LIFECYCLE_STATES: set[str] = {
    "uploaded",
    "shadow",
    "paper",
    "live",
    "scaled",
    "demoted",
    "offline",
    "retired",
}

WATCHLIST_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "NVDA",
    "MSFT",
    "TSLA",
    "AMZN",
    "GOOGL",
    "META",
    "AMD",
    "JPM",
    "XOM",
)

COMMODITIES: tuple[dict[str, str], ...] = (
    {"symbol": "GLD", "name": "Gold"},
    {"symbol": "SLV", "name": "Silver"},
    {"symbol": "USO", "name": "Crude Oil"},
    {"symbol": "UNG", "name": "Natural Gas"},
    {"symbol": "CPER", "name": "Copper"},
    {"symbol": "LIT", "name": "Lithium"},
)


class OrderSideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    quantity: float = Field(default=1.0, gt=0.0)
    score: float = Field(default=0.9, ge=0.0, le=1.0)
    signal_id: str | None = None


class ClosePositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = None


class TradingChangeChecklistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str = Field(min_length=3, max_length=120)
    phase: str = Field(description="pre|post")
    scope: list[str] = Field(default_factory=list, max_length=20)
    tests_passed: list[str] = Field(default_factory=list, max_length=50)
    runtime_evidence: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=500)


@router.get("/api/regime")
async def api_regime(container: Container = Depends(get_container)) -> dict[str, object]:
    payload = await IntelligenceService().regime_snapshot(container)
    compatibility_map = {"BULL": "risk_on", "BEAR": "risk_off", "NEUTRAL": "neutral", "CRISIS": "risk_off"}
    payload["compat_regime"] = compatibility_map.get(str(payload.get("regime", "NEUTRAL")), "neutral")
    return payload


@router.get("/summary")
@router.get("/api/summary")
async def summary_compat(container: Container = Depends(get_container)) -> dict[str, object]:
    portfolio_payload = await _safe_latest_portfolio_payload()
    account_raw = _account_from_portfolio_payload(portfolio_payload, container_broker_mode=container.broker_router.default_broker_name)
    positions_raw = portfolio_payload.get("positions", []) if isinstance(portfolio_payload, dict) else []
    if not isinstance(positions_raw, list):
        positions_raw = []
    signals = await _safe_recent_signals(limit=200)
    profiles = await _safe_list_profiles()

    equity = _as_float(account_raw.get("equity"))
    last_equity = _as_float(account_raw.get("last_equity"))
    cash = _as_float(account_raw.get("cash"))
    buying_power = _as_float(account_raw.get("buying_power")) or cash
    daily_pl = equity - last_equity
    daily_pl_pct = (daily_pl / last_equity * 100.0) if last_equity > 0 else 0.0

    positions: list[dict[str, object]] = []
    position_market_value = 0.0
    unrealized_pl = 0.0
    for row in positions_raw:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).upper()
        qty = _as_float(row.get("quantity", row.get("qty")))
        avg_entry = _as_float(row.get("avg_entry_price", row.get("avg_entry")))
        current_price = _as_float(row.get("current_price", row.get("last_price")))
        market_value = _as_float(row.get("market_value"))
        if market_value == 0.0 and current_price > 0.0:
            market_value = abs(qty) * current_price
        upnl = _as_float(row.get("unrealized_pl"))
        position_market_value += abs(market_value)
        unrealized_pl += upnl
        positions.append(
            {
                "symbol": symbol,
                "qty": qty,
                "avg_entry_price": avg_entry,
                "current_price": current_price,
                "market_value": round(market_value, 4),
                "unrealized_pl": round(upnl, 4),
            }
        )

    signal_rows = [
        {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "action": signal.action.value,
            "status": signal.status.value,
            "score": signal.score,
            "confidence": signal.confidence,
            "source": signal.source,
            "created_at": signal.created_at.isoformat(),
        }
        for signal in signals
    ]
    agents = [
        {
            "bot_id": bot_id,
            "display_name": str(profile.get("display_name", bot_id)),
            "lifecycle_state": str(profile.get("lifecycle_state", "unknown")),
            "status": "live" if bool(profile.get("enabled", False)) else "idle",
        }
        for bot_id, profile in profiles.items()
    ]
    agents.sort(key=lambda item: str(item["bot_id"]))
    pending_signals = sum(1 for row in signal_rows if str(row.get("status")) == "pending")
    as_of = datetime.now(timezone.utc).isoformat()
    account = {
        "equity": round(equity, 4),
        "last_equity": round(last_equity, 4),
        "cash": round(cash, 4),
        "buying_power": round(buying_power, 4),
        "pnl_today": round(daily_pl, 4),
        "pnl_today_pct": round(daily_pl_pct, 4),
        "mode": str(account_raw.get("mode", container.broker_router.default_broker_name)),
        "currency": str(account_raw.get("currency", "USD")),
        "daytrade_count": _as_int(account_raw.get("daytrade_count")),
    }
    return {
        "account": account,
        "equity": account["equity"],
        "last_equity": account["last_equity"],
        "cash": account["cash"],
        "buying_power": account["buying_power"],
        "daily_pl": round(daily_pl, 4),
        "daily_pl_pct": round(daily_pl_pct, 4),
        "position_market_value": round(position_market_value, 4),
        "unrealized_pl": round(unrealized_pl, 4),
        "position_count": len(positions),
        "positions": positions,
        "signals": signal_rows,
        "signals_pending": pending_signals,
        "agents": agents,
        "primary_broker": "alpaca",
        "as_of": as_of,
        "_ts": as_of,
        "generated_at": as_of,
    }


@router.get("/api/snapshot")
async def api_snapshot_compat(container: Container = Depends(get_container)) -> dict[str, object]:
    account_raw = await _safe_broker_account(container)
    equity = _as_float(account_raw.get("equity"))
    last_equity = _as_float(account_raw.get("last_equity"))
    cash = _as_float(account_raw.get("cash"))
    buying_power = _as_float(account_raw.get("buying_power"))
    daily_pnl = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity * 100.0) if last_equity > 0 else 0.0
    return {
        "status": "FRESH",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "equity": round(equity, 4),
        "last_equity": round(last_equity, 4),
        "cash": round(cash, 4),
        "buying_power": round(buying_power, 4),
        "daily_pnl": round(daily_pnl, 4),
        "daily_pnl_pct": round(daily_pnl_pct, 4),
    }


@router.get("/api/reward/status")
async def reward_status_compat() -> dict[str, object]:
    perf = await _safe_performance_summary()
    scorecards = await _safe_scorecards_by_bot(limit=800)
    total_processed = _as_int(perf.get("total_trades"))
    reward_score = round((_as_float(perf.get("win_rate")) * 100.0) + _as_float(perf.get("avg_pnl_pct")), 4)
    bot_performance = _reward_bot_performance(scorecards)
    cumulative_r = round(sum(_as_float(row.get("total_r")) for row in bot_performance.values()), 4)
    avg_r_per_trade = round((cumulative_r / total_processed), 4) if total_processed > 0 else 0.0
    return {
        "status": "active",
        "enabled": True,
        "total_trades_processed": total_processed,
        "wins": _as_int(perf.get("wins")),
        "losses": _as_int(perf.get("losses")),
        "win_rate": _as_float(perf.get("win_rate")),
        "avg_pnl_pct": _as_float(perf.get("avg_pnl_pct")),
        "reward_score": reward_score,
        "tracked_bots": len(scorecards),
        "bot_performance": bot_performance,
        "cumulative_r": cumulative_r,
        "avg_r_per_trade": avg_r_per_trade,
        "patterns_tracked": len(bot_performance),
        "lessons_stored": total_processed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/regime/status")
async def api_regime_status(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().regime_status(container)


@router.get("/api/correlation/status")
async def correlation_status() -> dict[str, object]:
    return await IntelligenceService().correlation_status()


@router.get("/api/correlation/matrix")
async def correlation_matrix(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().correlation_matrix(container)


@router.get("/api/correlation/check/{symbol}")
async def correlation_check(symbol: str, container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().correlation_check(symbol, container)


@router.get("/api/filters/status")
async def filters_status() -> dict[str, object]:
    return await IntelligenceService().filters_status()


@router.get("/api/filters/check/{symbol}")
async def filters_check(symbol: str, container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().filters_check(symbol, container)


@router.get("/api/pdt/status")
async def pdt_status(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().pdt_status(container)


@router.get("/api/earnings/check/{symbol}")
async def earnings_check(symbol: str) -> dict[str, object]:
    return await IntelligenceService().earnings_check(symbol)


@router.get("/api/permits/status")
async def permits_status() -> dict[str, object]:
    return await IntelligenceService().permits_status()


@router.get("/permits")
async def permits_alias() -> dict[str, object]:
    return await permits_status()


@router.get("/api/ml/status")
async def ml_status(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().ml_status()


@router.get("/api/ml/scores")
async def ml_scores(container: Container = Depends(get_container), limit: int = 20) -> dict[str, object]:
    return await IntelligenceService().ml_scores(container, limit=limit)


@router.post("/api/ml/evaluate")
async def ml_evaluate(symbol: str = "AAPL", container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().ml_evaluate(symbol, container)


@router.get("/api/llm/status")
async def llm_status(container: Container = Depends(get_container)) -> dict[str, object]:
    return await IntelligenceService().llm_status(container)


@router.get("/api/council/status")
async def council_status() -> dict[str, object]:
    return await IntelligenceService().council_status()


@router.get("/api/outcomes/status")
async def outcomes_status() -> dict[str, object]:
    return await IntelligenceService().outcomes_status()


@router.get("/api/bots/fleet")
async def bots_fleet(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    scheduler_active = _scheduler_active(container)
    rows: list[dict[str, object]] = []
    for bot_id, profile in profiles.items():
        enabled = bool(profile.get("enabled", False))
        lifecycle = str(profile.get("lifecycle_state", "unknown")).strip().lower()
        runtime_status = _runtime_status(
            lifecycle=lifecycle,
            enabled=enabled,
            scheduler_active=scheduler_active,
        )
        rows.append(
            {
                "bot_id": bot_id,
                "display_name": str(profile.get("display_name", bot_id)),
                "strategy_type": str(profile.get("strategy_type", "unknown")),
                "lifecycle_state": lifecycle,
                "status": runtime_status,
                "runtime_status": runtime_status,
                "enabled": enabled,
                "broker": str(profile.get("broker", "paper")),
                "scheduler_active": scheduler_active,
            }
        )
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {
        "fleet": rows,
        "count": len(rows),
        "status_source": "enabled+lifecycle+scheduler",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/bot/fleet")
async def bot_fleet_alias(container: Container = Depends(get_container)) -> dict[str, object]:
    return await bots_fleet(container)


@router.get("/api/bots/scorecards")
async def bots_scorecards() -> dict[str, object]:
    scorecards = await _safe_scorecards_by_bot(limit=800)
    rows = [{"bot_id": bot_id, **payload} for bot_id, payload in scorecards.items()]
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {"scorecards": rows, "count": len(rows), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/measurement/scorecards")
async def measurement_scorecards(days: int = 7) -> dict[str, object]:
    scorecards = await _safe_minimal_scorecards_by_bot(limit=2000, days=max(1, min(days, 90)))
    rows = [{"bot_id": bot_id, **payload} for bot_id, payload in scorecards.items()]
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {
        "scorecards": rows,
        "count": len(rows),
        "window_days": max(1, min(days, 90)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/measurement/scorecards/{bot_id}")
async def measurement_scorecard(bot_id: str, days: int = 7) -> dict[str, object]:
    scorecards = await _safe_minimal_scorecards_by_bot(limit=2000, days=max(1, min(days, 90)))
    scorecard = scorecards.get(bot_id)
    if scorecard is None:
        return {"error": f"bot_id '{bot_id}' not found"}
    return {
        "bot_id": bot_id,
        "window_days": max(1, min(days, 90)),
        "scorecard": scorecard,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/bots/{bot_id}/scorecard")
async def bot_scorecard(bot_id: str) -> dict[str, object]:
    scorecards = await _safe_scorecards_by_bot(limit=800)
    scorecard = scorecards.get(bot_id)
    if scorecard is None:
        return {"error": f"bot_id '{bot_id}' not found"}
    return {"bot_id": bot_id, "scorecard": scorecard, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/bots/{bot_id}/detail")
async def bot_detail(bot_id: str) -> dict[str, object]:
    profile = await _safe_get_profile(bot_id)
    scorecards = await _safe_scorecards_by_bot(limit=800)
    if profile is None:
        return {"error": f"bot_id '{bot_id}' not found"}
    return {
        "bot_id": bot_id,
        "profile": profile,
        "scorecard": scorecards.get(bot_id, _empty_scorecard()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/bots/{bot_id}/promote")
async def bot_promote(bot_id: str) -> dict[str, object]:
    return await _update_bot_lifecycle(bot_id, lifecycle_state="live", enabled=True, action="promoted")


@router.post("/api/bots/{bot_id}/demote")
async def bot_demote(bot_id: str) -> dict[str, object]:
    return await _update_bot_lifecycle(bot_id, lifecycle_state="demoted", enabled=False, action="demoted")


@router.post("/api/bots/{bot_id}/enable")
async def bot_enable(bot_id: str) -> dict[str, object]:
    return await _update_bot_lifecycle(bot_id, lifecycle_state=None, enabled=True, action="enabled")


@router.post("/api/bots/{bot_id}/disable")
async def bot_disable(bot_id: str) -> dict[str, object]:
    return await _update_bot_lifecycle(bot_id, lifecycle_state="offline", enabled=False, action="disabled")


@router.post("/api/strategies/{strategy_id}/promote")
async def strategy_promote(strategy_id: str) -> dict[str, object]:
    return await _update_strategy_lifecycle(strategy_id, lifecycle_state="live", action="promoted")


@router.post("/api/strategies/{strategy_id}/demote")
async def strategy_demote(strategy_id: str) -> dict[str, object]:
    return await _update_strategy_lifecycle(strategy_id, lifecycle_state="demoted", action="demoted")


@router.post("/api/strategies/{strategy_id}/retire")
async def strategy_retire(strategy_id: str) -> dict[str, object]:
    return await _update_strategy_lifecycle(strategy_id, lifecycle_state="retired", action="retired")


@router.post("/api/factory/candidate/{candidate_id}/admit-as-strategy")
async def admit_candidate_as_strategy(candidate_id: str) -> dict[str, object]:
    strategy_id = f"candidate:{candidate_id}"
    metadata: dict[str, JSONValue] = {
        "name": f"Candidate {candidate_id}",
        "version": "v1",
        "lifecycle_state": "shadow",
        "bot_ids": [],
        "notes": "admitted via reference compatibility endpoint",
    }
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            await repo.upsert_strategy_registry(strategy_id, metadata)
    except Exception as exc:
        return {"admitted": False, "candidate_id": candidate_id, "error": str(exc)[:200]}
    return {"admitted": True, "candidate_id": candidate_id, "strategy_id": strategy_id}


@router.get("/api/tradecopy/status")
async def tradecopy_status() -> dict[str, object]:
    return await _lane_status(bot_id="copycat", lane="tradecopy")


@router.get("/api/tradecopy/scan")
async def tradecopy_scan() -> dict[str, object]:
    return await _lane_scan(lane="tradecopy")


@router.post("/api/tradecopy/scan/run")
async def tradecopy_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="tradecopy", container=container)


@router.get("/api/copycat/status")
async def copycat_status() -> dict[str, object]:
    return await _lane_status(bot_id="copycat", lane="copycat")


@router.get("/api/copycat/scan")
async def copycat_scan() -> dict[str, object]:
    return await _lane_scan(lane="copycat")


@router.post("/api/copycat/scan/run")
async def copycat_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="copycat", container=container)


@router.get("/api/options/status")
async def options_status() -> dict[str, object]:
    return await _lane_status(bot_id="gambler", lane="options")


@router.get("/api/options/scan")
async def options_scan() -> dict[str, object]:
    return await _lane_scan(lane="options")


@router.post("/api/options/scan/run")
async def options_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="options", container=container)


@router.get("/api/gambler/status")
async def gambler_status() -> dict[str, object]:
    return await _lane_status(bot_id="gambler", lane="gambler")


@router.get("/api/gambler/scan")
async def gambler_scan() -> dict[str, object]:
    return await _lane_scan(lane="gambler")


@router.post("/api/gambler/scan/run")
async def gambler_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="gambler", container=container)


@router.get("/api/swingtrade/status")
async def swingtrade_status() -> dict[str, object]:
    return await _lane_status(bot_id="drifter", lane="swingtrade")


@router.get("/api/swingtrade/scan")
async def swingtrade_scan() -> dict[str, object]:
    return await _lane_scan(lane="swingtrade")


@router.post("/api/swingtrade/scan/run")
async def swingtrade_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="swingtrade", container=container)


@router.get("/api/drifter/status")
async def drifter_status() -> dict[str, object]:
    return await _lane_status(bot_id="drifter", lane="drifter")


@router.get("/api/drifter/scan")
async def drifter_scan() -> dict[str, object]:
    return await _lane_scan(lane="drifter")


@router.post("/api/drifter/scan/run")
async def drifter_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="drifter", container=container)


@router.get("/api/miner/status")
async def miner_status() -> dict[str, object]:
    return await _lane_status(bot_id="nugget_bot", lane="miner")


@router.post("/api/miner/scan")
async def miner_scan(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="miner", container=container)


@router.get("/api/nugget/status")
async def nugget_status() -> dict[str, object]:
    return await _lane_status(bot_id="nugget_bot", lane="nugget")


@router.get("/api/ausmining/status")
async def ausmining_status() -> dict[str, object]:
    base = await nugget_status()
    return {**base, "lane": "ausmining"}


@router.get("/api/evo/status")
async def evo_status() -> dict[str, object]:
    return await _lane_status(bot_id="evo_catalyst", lane="evo_catalyst")


@router.get("/api/evo/scan")
async def evo_scan() -> dict[str, object]:
    return await _lane_scan(lane="evo_catalyst")


@router.post("/api/evo/scan/run")
async def evo_scan_run(container: Container = Depends(get_container)) -> dict[str, object]:
    return await _lane_scan_run(lane="evo_catalyst", container=container)


@router.get("/api/nugget/scan")
async def nugget_scan() -> dict[str, object]:
    return await _lane_scan(lane="nugget")


@router.get("/api/nugget/signals")
async def nugget_signals(limit: int = 20) -> dict[str, object]:
    safe_limit = max(1, min(limit, 200))
    rows = await _safe_recent_signals(limit=safe_limit * 3)
    filtered: list[dict[str, object]] = []
    for signal in rows:
        symbol = signal.symbol.upper()
        if symbol.endswith(".AX") or symbol in {"RIO", "BHP", "FMG", "LIT"}:
            filtered.append(
                {
                    "signal_id": signal.signal_id,
                    "symbol": symbol,
                    "action": signal.action.value,
                    "status": signal.status.value,
                    "score": signal.score,
                    "created_at": signal.created_at.isoformat(),
                }
            )
        if len(filtered) >= safe_limit:
            break
    return {"signals": filtered, "count": len(filtered), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/commodities")
async def commodities() -> dict[str, object]:
    return {"commodities": list(COMMODITIES), "count": len(COMMODITIES)}


@router.get("/api/execution/tape")
async def execution_tape(limit: int = 50) -> dict[str, object]:
    safe_limit = max(1, min(limit, 500))
    try:
        rows = (
            await ExecutionRecord.all()
            .prefetch_related("order")
            .order_by("-created_at")
            .limit(safe_limit)
        )
    except Exception:
        rows = []
    tape: list[dict[str, object]] = []
    for row in rows:
        order_id = _execution_order_id(row)
        tape.append(
            {
                "execution_id": int(row.id),
                "order_id": order_id,
                "broker_order_id": row.broker_order_id,
                "status": row.status,
                "filled_qty": float(row.filled_qty),
                "avg_price": float(row.avg_price) if row.avg_price is not None else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"tape": tape, "count": len(tape), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/exit-guard/status")
async def exit_guard_status() -> dict[str, object]:
    return {
        "enabled": True,
        "protective_exits": True,
        "council_required_for_non_protective": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/positions/close")
async def positions_close(
    body: ClosePositionRequest | None = None,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    positions = await _safe_broker_positions(container)
    target = body.symbol.upper() if body is not None and body.symbol is not None else None
    submitted: list[dict[str, object]] = []
    for position in positions:
        symbol = str(position.get("symbol", "")).upper()
        if not symbol:
            continue
        if target is not None and symbol != target:
            continue
        qty = _as_float(position.get("quantity", position.get("qty")))
        if qty <= 0:
            continue
        close_signal = Signal(
            signal_id=f"close:{symbol}:{uuid4().hex[:12]}",
            symbol=symbol,
            action=OrderAction.SELL,
            score=1.0,
            confidence=1.0,
            source="manual",
            lane_hint="manual",
            strategy_hint="position_close",
            headline=f"Manual close for {symbol}",
            status=SignalStatus.PENDING,
        )
        try:
            await SignalService().ingest_signal(close_signal)
            execution = await submit_order(
                close_signal,
                quantity=qty,
                broker_router=container.broker_router,
            )
        except Exception as exc:
            submitted.append({"symbol": symbol, "submitted": False, "error": str(exc)[:200]})
            continue
        submitted.append(
            {
                "symbol": symbol,
                "submitted": execution is not None,
                "signal_id": close_signal.signal_id,
                "order_id": None if execution is None else execution.order_id,
                "status": None if execution is None else execution.status.value,
                "quantity": qty,
            }
        )
    return {
        "submitted": any(bool(item.get("submitted")) for item in submitted),
        "positions_targeted": len(submitted),
        "orders": submitted,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/orders")
async def list_orders(limit: int = 100) -> dict[str, object]:
    safe_limit = max(1, min(limit, 500))
    try:
        rows = await OrderRecord.all().order_by("-created_at").limit(safe_limit)
    except Exception:
        rows = []
    orders = [
        {
            "id": int(row.id),
            "signal_id": _order_signal_id(row),
            "idempotency_key": row.idempotency_key,
            "symbol": row.symbol,
            "action": row.action,
            "quantity": float(row.quantity),
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"orders": orders, "count": len(orders), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.post("/orders/buy")
async def order_buy(body: OrderSideRequest, container: Container = Depends(get_container)) -> dict[str, object]:
    return await _submit_manual_order(body=body, action=OrderAction.BUY, container=container)


@router.post("/orders/sell")
async def order_sell(body: OrderSideRequest, container: Container = Depends(get_container)) -> dict[str, object]:
    return await _submit_manual_order(body=body, action=OrderAction.SELL, container=container)


@router.get("/positions")
async def positions_alias(container: Container = Depends(get_container)) -> dict[str, object]:
    positions = await _safe_broker_positions(container)
    return {"positions": positions, "count": len(positions), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/watchlist")
async def watchlist_alias(container: Container = Depends(get_container)) -> dict[str, object]:
    held = {str(row.get("symbol", "")).upper() for row in await _safe_broker_positions(container)}
    rows = [{"symbol": symbol, "held": symbol in held} for symbol in WATCHLIST_SYMBOLS]
    return {"watchlist": rows, "count": len(rows), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/portfolio/history")
async def portfolio_history(limit: int = 50) -> dict[str, object]:
    safe_limit = max(1, min(limit, 200))
    try:
        async with UnitOfWork() as uow:
            repo = PositionSnapshotsRepository(connection=uow.connection)
            rows = await repo.list_recent(limit=safe_limit)
    except Exception:
        rows = []
    return {"history": rows, "count": len(rows), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/audit")
async def api_audit(limit: int = 100) -> dict[str, object]:
    safe_limit = max(1, min(limit, 500))
    try:
        rows = await AuditLog.all().order_by("-created_at").limit(safe_limit)
    except Exception:
        rows = []
    logs = [
        {
            "id": int(row.id),
            "trace_id": row.trace_id,
            "actor": row.actor,
            "event_type": row.event_type,
            "payload": row.payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"logs": logs, "count": len(logs), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/proof/runtime")
async def runtime_proof_pack_latest() -> dict[str, object]:
    latest = await _safe_latest_runtime_proof_pack()
    if latest is None:
        return {
            "status": "missing",
            "event_type": "proof.runtime.pack",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "status": "ok",
        "event_type": "proof.runtime.pack",
        "created_at": latest.get("created_at"),
        "payload": latest.get("payload", {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/proof/auto-exits")
async def auto_exit_proof(limit: int = 100) -> dict[str, object]:
    safe_limit = max(1, min(limit, 500))
    rows = await _safe_list_auto_exit_evidence(limit=safe_limit)
    return {
        "items": rows,
        "count": len(rows),
        "event_prefix": "proof.auto_exit.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/governance/trading-change/checklist")
async def save_trading_change_checklist(body: TradingChangeChecklistRequest) -> dict[str, object]:
    phase = body.phase.strip().lower()
    if phase not in {"pre", "post"}:
        return {"saved": False, "error": "phase must be 'pre' or 'post'"}
    payload = {
        "change_id": body.change_id.strip(),
        "phase": phase,
        "scope": [item.strip() for item in body.scope if item.strip()],
        "tests_passed": [item.strip() for item in body.tests_passed if item.strip()],
        "runtime_evidence": [item.strip() for item in body.runtime_evidence if item.strip()],
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            record_id = await repo.append(
                event_type="governance.trading_change_checklist",
                actor="api_extra",
                payload=_json_payload(payload),
            )
    except Exception as exc:
        memory_item = {
            "id": f"memory:{uuid4().hex[:12]}",
            "event_type": "governance.trading_change_checklist",
            "payload": payload,
            "created_at": payload["created_at"],
        }
        MEMORY_TRADING_CHANGE_CHECKLISTS.insert(0, memory_item)
        return {
            "saved": True,
            "id": memory_item["id"],
            "change_id": payload["change_id"],
            "phase": phase,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "storage": "memory_fallback",
            "warning": str(exc)[:200],
        }
    return {
        "saved": True,
        "id": record_id,
        "change_id": payload["change_id"],
        "phase": phase,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/governance/trading-change/checklist")
async def list_trading_change_checklist(limit: int = 20) -> dict[str, object]:
    safe_limit = max(1, min(limit, 200))
    rows = await _safe_list_trading_change_checklists(limit=safe_limit)
    if len(rows) < safe_limit and MEMORY_TRADING_CHANGE_CHECKLISTS:
        needed = safe_limit - len(rows)
        rows.extend(MEMORY_TRADING_CHANGE_CHECKLISTS[:needed])
    return {
        "items": rows,
        "count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/tuning")
async def api_tuning() -> dict[str, object]:
    perf = await _safe_performance_summary()
    win_rate = _as_float(perf.get("win_rate"))
    avg_pnl_pct = _as_float(perf.get("avg_pnl_pct"))
    signal_threshold = 0.5
    if win_rate >= 0.55:
        signal_threshold = 0.62
    elif win_rate <= 0.45:
        signal_threshold = 0.72
    risk_multiplier = 1.0
    if avg_pnl_pct < 0.0:
        risk_multiplier = 0.7
    elif avg_pnl_pct > 1.0:
        risk_multiplier = 1.1
    return {
        "mode": "derived_from_recent_performance",
        "parameters": {"risk_multiplier": round(risk_multiplier, 4), "signal_threshold": round(signal_threshold, 4)},
        "inputs": perf,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/arena")
async def api_arena() -> dict[str, object]:
    strategies = await _safe_list_strategies()
    return {
        "status": "ready",
        "strategies_total": len(strategies),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/shadow")
async def api_shadow() -> dict[str, object]:
    strategies = await _safe_list_strategies()
    shadow = [sid for sid, meta in strategies.items() if str(meta.get("lifecycle_state", "")).lower() == "shadow"]
    return {"shadow_strategies": shadow, "count": len(shadow), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/doctor/fixes")
async def api_doctor_fixes() -> dict[str, object]:
    fixes: list[dict[str, object]] = []
    profiles = await _safe_list_profiles()
    if not profiles:
        fixes.append(
            {
                "id": "seed-bot-profiles",
                "severity": "warn",
                "action": "run `uv run python scripts/seed_registry.py --mode repair`",
            }
        )
    outcomes = await _safe_recent_outcomes(limit=20)
    if not outcomes:
        fixes.append({"id": "collect-trade-outcomes", "severity": "info", "action": "run paper flow to populate outcomes"})
    return {
        "status": "ok",
        "pending_fixes": fixes,
        "last_check": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/cache/clear")
async def api_cache_clear() -> dict[str, object]:
    cleared = ScanService.clear_memory_state()
    return {
        "cleared": True,
        "scope": "scan_service_memory_state",
        "details": cleared,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/bars/{symbol}")
async def bars_symbol(
    symbol: str,
    container: Container = Depends(get_container),
    limit: int = 60,
) -> dict[str, object]:
    safe_limit = max(5, min(limit, 500))
    quote = await _safe_broker_quote(container, symbol.upper())
    raw_bars = await MarketDataService().fetch_daily_bars(symbol.upper(), range_name="6mo")
    if not raw_bars:
        return {
            "symbol": symbol.upper(),
            "status": "unavailable",
            "bars": [],
            "count": 0,
            "last": _as_float(quote.get("last")),
            "bid": _as_float(quote.get("bid")),
            "ask": _as_float(quote.get("ask")),
            "source": "quote_only",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    bars = [
        {
            "t": row.get("timestamp"),
            "c": round(_as_float(row.get("close")), 4),
            "v": _as_int(row.get("volume")),
        }
        for row in raw_bars[-safe_limit:]
    ]
    return {
        "symbol": symbol.upper(),
        "status": "ok",
        "bars": bars,
        "count": len(bars),
        "last": _as_float(quote.get("last")),
        "bid": _as_float(quote.get("bid")),
        "ask": _as_float(quote.get("ask")),
        "source": "yahoo_chart_daily",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/ecosystem")
async def api_ecosystem(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    strategies = await _safe_list_strategies()
    perf = await _safe_performance_summary()
    return {
        "bots": {"count": len(profiles), "active": sum(1 for p in profiles.values() if bool(p.get("enabled", False)))},
        "strategies": {"count": len(strategies)},
        "runtime": {
            "queue": container.queue_bus.snapshot_sizes(),
            "scheduler": container.scheduler.snapshot() if container.scheduler is not None else {"enabled": False, "job_count": 0},
            "trading_halted": container.trading_halted,
        },
        "performance": perf,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/fleet/edge-status")
async def fleet_edge_status() -> dict[str, object]:
    scorecards = await _safe_scorecards_by_bot(limit=800)
    rows: list[dict[str, object]] = []
    for bot_id, card in scorecards.items():
        win_rate = _as_float(card.get("win_rate"))
        if win_rate >= 0.55:
            edge = "positive"
        elif win_rate <= 0.45:
            edge = "negative"
        else:
            edge = "neutral"
        rows.append({"bot_id": bot_id, "edge": edge, **card})
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {"fleet": rows, "count": len(rows), "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/fleet/allocation")
async def fleet_allocation() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    rows: list[dict[str, object]] = []
    total = 0.0
    for bot_id, profile in profiles.items():
        allocation = profile.get("allocation")
        pct = 0.0
        if isinstance(allocation, dict):
            raw = allocation.get("capital_pct")
            if isinstance(raw, (float, int)):
                pct = float(raw)
        total += pct
        rows.append({"bot_id": bot_id, "capital_pct": round(pct, 4)})
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {"allocation": rows, "declared_total_pct": round(total, 4), "count": len(rows)}


@router.get("/api/fleet/risk")
async def fleet_risk(container: Container = Depends(get_container)) -> dict[str, object]:
    from app.services.runtime_config.service import RuntimeConfigService

    runtime_configs = RuntimeConfigService()
    max_daily_loss = await runtime_configs.resolve_float("risk.max_daily_loss_pct")
    max_position = await runtime_configs.resolve_float("risk.max_position_pct")
    max_open_positions = await runtime_configs.resolve("risk.max_open_positions")

    daily_loss_cap = round(float(max_daily_loss.value or 0.05) * 100.0, 2)
    max_positions = int(max_open_positions.value or 15)
    max_per_bot = round(float(max_position.value or 0.25) * 100.0, 2)

    current_equity = 0.0
    last_equity = 0.0
    total_positions = 0
    for name in container.broker_router.list_brokers():
        broker = container.broker_router.get(name)
        if broker is not None:
            try:
                acct = await broker.get_account()
                current_equity += float(acct.get("equity", 0.0) or 0.0)
                last_equity += float(acct.get("last_equity", 0.0) or acct.get("equity", 0.0) or 0.0)
                pos = await broker.get_positions()
                total_positions += len([row for row in pos if float(row.get("qty", row.get("quantity", 0.0))) != 0.0])
            except Exception:
                pass

    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)
    
    async with UnitOfWork() as uow:
        repo = PositionSnapshotsRepository(connection=uow.connection)
        snapshots = await repo.list_since(cutoff_30d, limit=5000)

    snapshot_equities: list[tuple[str, float]] = []
    for snap in snapshots:
        payload = snap.get("payload")
        if isinstance(payload, dict):
            eq = payload.get("equity")
            created_at = snap.get("created_at")
            if isinstance(eq, (int, float)) and isinstance(created_at, str):
                snapshot_equities.append((created_at, float(eq)))

    snapshot_equities.sort(key=lambda x: x[0])

    peak_equity = max([eq for _, eq in snapshot_equities] + [current_equity]) if snapshot_equities else current_equity
    drawdown_pct = round(((peak_equity - current_equity) / peak_equity) * 100.0, 2) if peak_equity > 0.0 else 0.0
    daily_pnl = round(current_equity - last_equity, 2)

    cutoff_7d = (now - timedelta(days=7)).isoformat()
    weekly_start = next((eq for ts, eq in snapshot_equities if ts >= cutoff_7d), None)
    weekly_pnl = round(current_equity - weekly_start, 2) if weekly_start is not None else daily_pnl

    monthly_start = snapshot_equities[0][1] if snapshot_equities else None
    monthly_pnl = round(current_equity - monthly_start, 2) if monthly_start is not None else daily_pnl

    return {
        "fleet_halted": container.trading_halted,
        "halt_reason": container.trading_halt_reason,
        "peak_equity": round(peak_equity, 2),
        "current_drawdown_pct": round(drawdown_pct, 2),
        "daily_pnl": round(daily_pnl, 2),
        "weekly_pnl": round(weekly_pnl, 2),
        "monthly_pnl": round(monthly_pnl, 2),
        "total_positions": total_positions,
        "limits": {
            "daily_loss_cap": daily_loss_cap,
            "weekly_loss_cap": 8.0,
            "monthly_loss_cap": 12.0,
            "max_drawdown": 15.0,
            "max_positions": max_positions,
            "max_per_bot": max_per_bot,
            "reserve_min": 10.0,
        },
        "generated_at": now.isoformat(),
    }


async def _submit_manual_order(
    *,
    body: OrderSideRequest,
    action: OrderAction,
    container: Container,
) -> dict[str, object]:
    signal_id = body.signal_id or f"manual:{body.symbol.upper()}:{uuid4().hex[:12]}"
    signal = Signal(
        signal_id=signal_id,
        symbol=body.symbol.upper(),
        action=action,
        score=body.score,
        confidence=body.score,
        source="manual",
        lane_hint="manual",
        strategy_hint="manual_order",
        headline=f"Manual {action.value} order for {body.symbol.upper()}",
        status=SignalStatus.PENDING,
    )
    try:
        await SignalService().ingest_signal(signal)
        execution = await submit_order(
            signal,
            quantity=body.quantity,
            broker_router=container.broker_router,
        )
    except Exception as exc:
        return {"submitted": False, "signal_id": signal_id, "error": str(exc)[:200]}
    if execution is None:
        return {"submitted": False, "signal_id": signal_id, "status": "rejected"}
    return {
        "submitted": True,
        "signal_id": signal_id,
        "order_id": execution.order_id,
        "broker_order_id": execution.broker_order_id,
        "status": execution.status.value,
        "filled_qty": execution.filled_qty,
        "avg_price": execution.avg_price,
    }


async def _update_bot_lifecycle(
    bot_id: str,
    *,
    lifecycle_state: str | None,
    enabled: bool,
    action: str,
) -> dict[str, object]:
    evidence = await GovernanceService().bot_lifecycle_evidence(bot_id)
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            profile = await repo.get_bot_profile(bot_id)
            if profile is None:
                return {"updated": False, "bot_id": bot_id, "error": "bot profile not found"}
            merged = _merge_json(profile)
            previous_lifecycle_state = str(merged.get("lifecycle_state", "unknown"))
            previous_enabled = bool(merged.get("enabled", False))
            merged["enabled"] = enabled
            if lifecycle_state is not None:
                normalized = lifecycle_state.strip().lower()
                if normalized not in LIFECYCLE_STATES:
                    return {"updated": False, "bot_id": bot_id, "error": f"invalid lifecycle_state '{normalized}'"}
                merged["lifecycle_state"] = normalized
            governance_state = _json_payload(
                {
                    "last_action": action,
                    "evidence": evidence,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            merged["governance_state"] = governance_state
            await repo.upsert_bot_profile(bot_id, merged)
            audit_payload = _json_payload(
                {
                    "bot_id": bot_id,
                    "action": action,
                    "previous_enabled": previous_enabled,
                    "enabled": enabled,
                    "previous_lifecycle_state": previous_lifecycle_state,
                    "lifecycle_state": merged.get("lifecycle_state"),
                    "evidence": evidence,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await AuditLogsRepository(connection=uow.connection).append(
                event_type="bot_lifecycle_action",
                actor="api_extra",
                payload=audit_payload,
            )
    except Exception as exc:
        return {"updated": False, "bot_id": bot_id, "error": str(exc)[:200]}
    return {
        "updated": True,
        "bot_id": bot_id,
        "action": action,
        "evidence": evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _update_strategy_lifecycle(strategy_id: str, *, lifecycle_state: str, action: str) -> dict[str, object]:
    normalized = lifecycle_state.strip().lower()
    if normalized not in LIFECYCLE_STATES:
        return {"updated": False, "strategy_id": strategy_id, "error": f"invalid lifecycle_state '{normalized}'"}
    evidence = await GovernanceService().strategy_lifecycle_evidence(strategy_id)
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            strategy = await repo.get_strategy_registry(strategy_id)
            if strategy is None:
                return {"updated": False, "strategy_id": strategy_id, "error": "strategy not found"}
            merged = _merge_json(strategy)
            previous_lifecycle_state = str(merged.get("lifecycle_state", "unknown"))
            merged["lifecycle_state"] = normalized
            merged["governance_state"] = _json_payload(
                {
                    "last_action": action,
                    "evidence": evidence,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            await repo.upsert_strategy_registry(strategy_id, merged)
            await AuditLogsRepository(connection=uow.connection).append(
                event_type="strategy_lifecycle_action",
                actor="api_extra",
                payload=_json_payload(
                    {
                        "strategy_id": strategy_id,
                        "action": action,
                        "previous_lifecycle_state": previous_lifecycle_state,
                        "lifecycle_state": normalized,
                        "evidence": evidence,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
    except Exception as exc:
        return {"updated": False, "strategy_id": strategy_id, "error": str(exc)[:200]}
    return {
        "updated": True,
        "strategy_id": strategy_id,
        "action": action,
        "evidence": evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _lane_status(*, bot_id: str, lane: str) -> dict[str, object]:
    profile = await _safe_get_profile(bot_id)
    enabled = bool(profile.get("enabled", False)) if profile is not None else False
    lifecycle_state = str(profile.get("lifecycle_state", "unknown")) if profile is not None else "unknown"
    payload = await ScanService().get_lane_status(
        lane=lane,
        bot_id=bot_id,
        enabled=enabled,
        lifecycle_state=lifecycle_state,
    )
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


async def _lane_scan(*, lane: str) -> dict[str, object]:
    return await ScanService().get_lane_scan(lane=lane)


async def _lane_scan_run(*, lane: str, container: Container) -> dict[str, object]:
    result = await ScanService().run_lane_scan(
        lane=lane,
        container=container,
        origin=f"api.lane_scan.{lane}",
    )
    result["triggered"] = True
    result["persisted"] = True
    result["event_type"] = f"{lane.replace('-', '_').title().replace('_', '')}ScanRequested"
    return result


async def _safe_list_profiles() -> dict[str, dict]:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.list_bot_profiles()
    except Exception:
        return {}


async def _safe_get_profile(bot_id: str) -> dict | None:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.get_bot_profile(bot_id)
    except Exception:
        return None


async def _safe_list_strategies() -> dict[str, dict]:
    try:
        async with UnitOfWork() as uow:
            repo = BotsRepository(connection=uow.connection)
            return await repo.list_strategy_registry()
    except Exception:
        return {}


async def _safe_recent_signals(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_recent_outcomes(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_latest_portfolio_payload() -> dict[str, object] | None:
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


def _account_from_portfolio_payload(
    payload: dict[str, object] | None,
    *,
    container_broker_mode: str,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    return {
        "equity": _as_float(payload.get("equity")),
        "last_equity": _as_float(payload.get("last_equity")),
        "cash": _as_float(payload.get("cash")),
        "buying_power": _as_float(payload.get("buying_power", payload.get("cash"))),
        "mode": str(payload.get("mode", container_broker_mode)),
        "currency": str(payload.get("currency", "USD")),
        "daytrade_count": _as_int(payload.get("daytrade_count")),
    }


async def _safe_performance_summary() -> dict[str, object]:
    outcomes = await _safe_recent_outcomes(limit=800)
    total = len(outcomes)
    wins = sum(1 for item in outcomes if item.outcome.value == "win")
    losses = sum(1 for item in outcomes if item.outcome.value == "loss")
    pnl_values = [float(item.pnl_pct) for item in outcomes if item.pnl_pct is not None]
    avg_pnl_pct = (sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0
    win_rate = (wins / total) if total > 0 else 0.0
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 6),
        "avg_pnl_pct": round(avg_pnl_pct, 6),
    }


async def _safe_broker_account(container: Container) -> dict[str, object]:
    try:
        account = await container.broker.get_account()
        return account if isinstance(account, dict) else {}
    except Exception:
        return {}


async def _safe_broker_positions(container: Container) -> list[dict[str, object]]:
    try:
        rows = await container.broker.get_positions()
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]
    except Exception:
        return []


async def _safe_broker_quote(container: Container, symbol: str) -> dict[str, object]:
    try:
        quote = await container.broker.get_quote(symbol)
        return quote if isinstance(quote, dict) else {}
    except Exception:
        return {}


async def _safe_scorecards_by_bot(limit: int) -> dict[str, _ScorecardSummary]:
    try:
        return await MeasurementService().scorecards_by_bot(limit=limit)
    except Exception:
        return {}


async def _safe_minimal_scorecards_by_bot(limit: int, days: int) -> dict[str, _ScorecardSummary]:
    try:
        return await MeasurementService().minimal_scorecards_by_bot(limit=limit, days=days)
    except Exception:
        return {}


def _reward_bot_performance(scorecards: dict[str, _ScorecardSummary]) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for bot_id, card in scorecards.items():
        trades = _as_int(card.get("total_trades"))
        avg_r = _as_float(card.get("expectancy_r"))
        total_r = round(avg_r * trades, 4) if trades > 0 else 0.0
        payload[bot_id] = {
            "trades": trades,
            "wins": _as_int(card.get("wins")),
            "losses": _as_int(card.get("losses")),
            "total_r": total_r,
            "avg_r": avg_r,
            "win_rate": _as_float(card.get("win_rate")),
        }
    return payload


async def _safe_latest_runtime_proof_pack() -> dict[str, object] | None:
    try:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            row = await repo.latest_by_type(event_type="proof.runtime.pack")
            if row is None:
                return None
            return {
                "id": int(row.id),
                "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                "payload": row.payload if isinstance(row.payload, dict) else {},
            }
    except Exception:
        return None


async def _safe_list_trading_change_checklists(limit: int) -> list[dict[str, object]]:
    try:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            rows = await repo.list_recent_by_prefix(
                prefix="governance.trading_change_checklist",
                limit=limit,
            )
            return [
                {
                    "id": int(row.id),
                    "event_type": row.event_type,
                    "payload": row.payload if isinstance(row.payload, dict) else {},
                    "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                }
                for row in rows
            ]
    except Exception:
        return []


async def _safe_list_auto_exit_evidence(limit: int) -> list[dict[str, object]]:
    try:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            rows = await repo.list_recent_by_prefix(prefix="proof.auto_exit.", limit=limit)
            return [
                {
                    "id": int(row.id),
                    "event_type": row.event_type,
                    "payload": row.payload if isinstance(row.payload, dict) else {},
                    "created_at": row.created_at.isoformat() if row.created_at is not None else None,
                }
                for row in rows
            ]
    except Exception:
        return []


def _runtime_status(*, lifecycle: str, enabled: bool, scheduler_active: bool) -> str:
    if lifecycle in {"offline", "retired", "demoted"}:
        return "offline"
    if not enabled:
        return "inactive"
    if scheduler_active:
        return "active"
    return "idle"


def _scheduler_active(container: Container) -> bool:
    if container.scheduler is None:
        return False
    snapshot = container.scheduler.snapshot()
    return bool(snapshot.get("enabled")) and bool(snapshot.get("active")) and int(snapshot.get("job_count", 0)) > 0


def _infer_bot_id(signal_id: str) -> str:
    lower = signal_id.lower()
    if "copycat" in lower or "tradecopy" in lower or "copy" in lower:
        return "copycat"
    if "gambler" in lower or "option" in lower:
        return "gambler"
    if "swing" in lower or "drifter" in lower:
        return "drifter"
    if "nugget" in lower or "mine" in lower:
        return "nugget_bot"
    if "evo" in lower:
        return "evo_catalyst"
    if "turbo" in lower:
        return "turbo"
    return "unknown"


def _lane_matches_signal(*, lane: str, signal: Signal) -> bool:
    normalized_lane = lane.strip().lower()
    signal_id = signal.signal_id.lower()
    symbol = signal.symbol.upper()
    if normalized_lane in signal_id:
        return True
    if normalized_lane in {"nugget", "miner"}:
        return symbol.endswith(".AX") or symbol in {"RIO", "BHP", "FMG", "LIT"}
    if normalized_lane in {"copycat", "tradecopy"}:
        return "copy" in signal_id
    if normalized_lane in {"options", "gambler"}:
        return "option" in signal_id or "gambler" in signal_id
    if normalized_lane in {"swingtrade", "drifter"}:
        return "swing" in signal_id or "drifter" in signal_id
    return False


def _empty_scorecard() -> dict[str, object]:
    return {
        "total_trades": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_pnl_pct": 0.0,
        "expectancy_usd": None,
        "expectancy_r": None,
        "profit_factor": None,
        "edge_status": "INSUFFICIENT_DATA",
        "quality_score": 0,
    }


def _regime_score(regime: str) -> int:
    if regime == "risk_on":
        return 78
    if regime == "risk_off":
        return 26
    return 52


def _default_bot_profiles() -> dict[str, dict[str, JSONValue]]:
    return CANONICAL_BOT_PROFILES


def _default_strategies() -> dict[str, dict[str, JSONValue]]:
    return CANONICAL_STRATEGIES
