from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from loguru import logger

from api.deps import get_container
from api.routers.control_plane_compat_utils import (
    as_float as _as_float,
    as_int as _as_int,
    as_optional_float as _as_optional_float,
    iso_now as _iso_now,
    object_dict as _object_dict,
    object_dict_rows as _object_dict_rows,
    object_list as _object_list,
)
from app.services.control_plane.service import RuntimeControlPlaneService
from app.services.control_plane.types import BrokerRow, ControlPlaneSnapshot, ProcessManagerSnapshot, SchedulerSnapshot
from app.services.governance.service import GovernanceService
from app.services.measurement.service import MeasurementService
from app.services.scan.service import ScanService
from db.models import ExecutionRecord, OrderRecord, SignalRecord, TradeOutcomeRecord
from db.repositories._common import append_outbox_event
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from runtime.container import Container

router = APIRouter(prefix="/api", tags=["control-plane-compat"])


@router.get("/agents/summary")
async def agents_summary(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    latest_activity = await _latest_activity_at()
    control_plane: ControlPlaneSnapshot = await RuntimeControlPlaneService().snapshot(container)
    total = len(profiles)
    enabled = sum(1 for profile in profiles.values() if bool(profile.get("enabled", False)))
    families = _family_counts(profiles)
    live = sum(1 for profile in profiles.values() if _runtime_status_for_profile(profile) == "live")
    idle = total - live
    duty_counts = control_plane["duty_counts"]
    reconcile = control_plane["reconcile"]
    alerts = control_plane["alerts"]
    return {
        "total_agents": total,
        "enabled_agents": enabled,
        "idle_agents": idle,
        "live": live,
        "idle": idle,
        "degraded": duty_counts["degraded"],
        "blocked": duty_counts["blocked"],
        "offline": sum(1 for profile in profiles.values() if _runtime_status_for_profile(profile) == "offline"),
        "last_sweep_at": latest_activity,
        "families": families,
        "fleet_status": {
            "fleet": "runtime_duties",
            "is_stale": reconcile["stale"],
            "runner_present": True,
            "warning": None if not alerts else str(alerts[0]["message"]),
        },
        "runtime_duties_total": duty_counts["total"],
        "runtime_duties_live": duty_counts["healthy"],
        "generated_at": _iso_now(),
        "runtime_queue": container.queue_bus.snapshot_sizes(),
    }


@router.get("/agents/fleet")
async def agents_fleet() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    fleet: list[dict[str, object]] = []
    for bot_id, profile in profiles.items():
        row = _build_agent_row(bot_id, profile)
        fleet.append(row)
    fleet.sort(key=lambda row: str(row["technical_id"]))
    return {"fleet": fleet, "agents": fleet, "count": len(fleet), "generated_at": _iso_now()}


@router.get("/agents/families")
async def agents_families() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    grouped: dict[str, dict[str, object]] = {}
    for bot_id, profile in profiles.items():
        family = _profile_family(profile)
        item = grouped.setdefault(
            family,
            {"family": family, "count": 0, "live": 0, "degraded": 0, "members": []},
        )
        item["count"] = _as_int(item.get("count")) + 1
        if _runtime_status_for_profile(profile) == "live":
            item["live"] = _as_int(item.get("live")) + 1
        members = item["members"]
        if isinstance(members, list):
            members.append(bot_id)
    rows = sorted(grouped.values(), key=lambda item: str(item["family"]))
    return {
        "families": rows,
        "family_counts": {str(item["family"]): _as_int(item.get("count")) for item in rows},
        "generated_at": _iso_now(),
    }


@router.get("/agents/{technical_id}")
async def agent_detail(technical_id: str) -> dict[str, object]:
    profile = await _safe_get_profile(technical_id)
    if profile is None:
        return {"error": f"agent '{technical_id}' not found"}
    row = _build_agent_row(technical_id, profile)
    row.update(
        {
            "purpose": f"{_profile_family(profile)} trading profile",
            "upstream": [_profile_market(profile)],
            "outputs": ["signals", "orders", "outcomes"],
            "linked_tasks": [],
            "last_proofs": [],
            "runtime_notes": f"lifecycle={_lifecycle_state(profile)} enabled={bool(profile.get('enabled', False))}",
            "profile": profile,
            "generated_at": _iso_now(),
        }
    )
    return row


@router.get("/control-plane/overview")
async def control_plane_overview(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    pending = await _safe_pending_count()
    control_plane: ControlPlaneSnapshot = await RuntimeControlPlaneService().snapshot(container)
    broker_rows: list[BrokerRow] = control_plane["brokers"]
    action_summary = await _monitor_actions_summary(container)
    return {
        "ruflo_mode": "active",
        "botux_mode": str(container.broker_router.default_broker_name).upper(),
        "market_state": "halted" if container.trading_halted else str(control_plane["status"]),
        "broker_state": {str(row["broker"]): ("connected" if bool(row["connected"]) else "disconnected") for row in broker_rows},
        "active_tasks": _scheduler_job_count(container),
        "blocked_tasks": 1 if container.trading_halted else 0,
        "quarantine_count": container.queue_bus.snapshot_sizes().get("dead_letter_queue", 0),
        "critical_alerts": max(_as_int(action_summary.get("high_priority_count")), control_plane["high_priority_count"]),
        "last_governance_sync": _iso_now(),
        "fleet_size": len(profiles),
        "signals_pending": pending,
        "queue": container.queue_bus.snapshot_sizes(),
        "trading_halted": container.trading_halted,
        "generated_at": _iso_now(),
    }


@router.get("/control-plane/fleet-state")
async def control_plane_fleet_state() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    units: list[dict[str, object]] = []
    for bot_id, profile in profiles.items():
        lifecycle = _lifecycle_state(profile)
        units.append(
            {
                "technical_id": bot_id,
                "display_name": str(profile.get("display_name", bot_id)),
                "class": "bot",
                "lock_state": lifecycle,
                "runtime_state": _runtime_status_for_profile(profile),
                "market": _profile_market(profile),
                "broker": _profile_broker(profile),
                "last_activity_at": None,
                "current_task_impact": [],
            }
        )
    units.sort(key=lambda item: str(item["technical_id"]))
    return {"units": units, "count": len(units), "generated_at": _iso_now()}


@router.get("/control-plane/runtime")
async def control_plane_runtime(container: Container = Depends(get_container)) -> dict[str, object]:
    control_plane: ControlPlaneSnapshot = await RuntimeControlPlaneService().snapshot(container)
    queue_snapshot = control_plane["queue"]
    broker_rows: list[BrokerRow] = control_plane["brokers"]
    db_truth = control_plane["db_truth"]
    return {
        "runtime_mode": "asyncio",
        "api_health": str(control_plane["status"]),
        "broker_connectivity": {str(row["broker"]): ("connected" if bool(row["connected"]) else "error") for row in broker_rows},
        "pipeline": {
            "pending_signals": db_truth["pending_signals"],
            "stale_signals": db_truth["stale_pending_15m"],
            "work_queue": queue_snapshot.get("work_queue", 0),
            "retry_queue": queue_snapshot.get("retry_queue", 0),
            "dead_letter_queue": queue_snapshot.get("dead_letter_queue", 0),
        },
        "quarantine_count": queue_snapshot.get("dead_letter_queue", 0),
        "open_positions": db_truth["open_outcomes"],
        "signals_pending": db_truth["pending_signals"],
        "stale_signals": db_truth["stale_pending_15m"],
        "council_error_rate": 0.0,
        "scheduler": control_plane["scheduler"],
        "workers": control_plane["workers"],
        "duties": control_plane["duties"],
        "alerts": control_plane["alerts"],
        "reconcile": control_plane["reconcile"],
        "queue": queue_snapshot,
        "last_updated_at": _iso_now(),
        "generated_at": _iso_now(),
    }


@router.get("/control-plane/tasks")
async def control_plane_tasks(container: Container = Depends(get_container)) -> dict[str, object]:
    pending = await _safe_pending_count()
    tasks: list[dict[str, object]] = [
        {
            "task_id": "process_pending_signals",
            "name": "process_pending_signals",
            "type": "runtime",
            "affected_units": [],
            "approval_state": "approved",
            "status": "in_progress" if pending > 0 else "closed",
            "proof_state": "complete" if pending == 0 else "pending",
            "current_phase": "execution" if pending > 0 else "idle",
            "ruling": "proceed",
            "pending": pending,
        }
    ]
    scheduler = _scheduler_snapshot(container)
    for job in scheduler["jobs"]:
        task_id = job["name"]
        tasks.append(
            {
                "task_id": task_id,
                "name": task_id,
                "type": "scheduler",
                "affected_units": [],
                "approval_state": "approved",
                "status": "active" if job["active"] else "idle",
                "proof_state": "complete",
                "current_phase": "scheduled",
                "ruling": "proceed",
            }
        )
    counts = {
        "open": sum(1 for item in tasks if str(item["status"]) not in {"closed", "idle"}),
        "blocked": 0,
        "approval_needed": 0,
        "closeout_ready": sum(1 for item in tasks if str(item["proof_state"]) == "complete"),
    }
    return {"tasks": tasks, "counts": counts, "generated_at": _iso_now()}


@router.get("/control-plane/proof")
async def control_plane_proof(container: Container = Depends(get_container)) -> dict[str, object]:
    queue_snapshot = container.queue_bus.snapshot_sizes()
    tasks = [
        {
            "task_id": "runtime-foundation",
            "test_proof": "make test",
            "endpoint_proof": "reference parity smoke",
            "runtime_proof": "asyncio workers active",
            "registry_proof": "tortoise repositories wired",
            "commit_proof": None,
            "closeout_ready": True,
        },
        {
            "task_id": "queue-health",
            "test_proof": f"work={queue_snapshot.get('work_queue', 0)}",
            "endpoint_proof": "GET /runtime/queues",
            "runtime_proof": "queue snapshot available",
            "registry_proof": None,
            "commit_proof": None,
            "closeout_ready": True,
        },
    ]
    return {
        "tasks": tasks,
        "proof": [
            {"item": "async_queue_runtime", "status": "ok"},
            {"item": "tortoise_repositories", "status": "ok"},
            {"item": "api_compat_layers", "status": "ok"},
        ],
        "generated_at": _iso_now(),
    }


@router.get("/governance/locks")
async def governance_locks(container: Container = Depends(get_container)) -> dict[str, object]:
    locks = [
        {
            "doc_id": "trading_halt",
            "status": "active" if container.trading_halted else "inactive",
            "detail": container.trading_halt_reason,
        }
    ]
    return {
        "locks": {
            "trading_halted": container.trading_halted,
            "halt_reason": container.trading_halt_reason,
            "halted_at": container.trading_halted_at,
        },
        "locks_list": locks,
        "generated_at": _iso_now(),
    }


@router.get("/governance/registry")
async def governance_registry() -> dict[str, object]:
    strategies = await _safe_list_strategies()
    readiness = await GovernanceService().promotion_readiness_report(persist=False)
    readiness_by_strategy = {
        str(item["strategy_id"]): item
        for item in _object_dict_rows(readiness.get("items"))
        if "strategy_id" in item
    }
    units: list[dict[str, object]] = []
    enriched_strategies: dict[str, dict[str, object]] = {}
    for strategy_id, strategy in strategies.items():
        evidence = readiness_by_strategy.get(strategy_id, {})
        stored_governance = strategy.get("governance_state") if isinstance(strategy.get("governance_state"), dict) else {}
        enriched = {
            **strategy,
            "governance": {
                "candidacy": evidence.get("candidacy"),
                "ready": evidence.get("ready"),
                "readiness_score": evidence.get("readiness_score"),
                "quality_tier": evidence.get("quality_tier"),
                "trade_count": evidence.get("trade_count"),
                "decay_severity": evidence.get("decay_severity"),
                "warnings": evidence.get("warnings", []),
                "last_action": stored_governance.get("last_action") if isinstance(stored_governance, dict) else None,
                "last_action_evidence": stored_governance.get("evidence") if isinstance(stored_governance, dict) else None,
                "last_updated_at": stored_governance.get("updated_at") if isinstance(stored_governance, dict) else None,
            },
        }
        enriched_strategies[strategy_id] = enriched
        units.append(
            {
                "technical_id": strategy_id,
                "class": "strategy",
                "lock_state": str(strategy.get("lifecycle_state", "unknown")),
                "display_name": str(strategy.get("name", strategy_id)),
                "candidacy": evidence.get("candidacy"),
                "ready": evidence.get("ready"),
                "readiness_score": evidence.get("readiness_score"),
                "quality_tier": evidence.get("quality_tier"),
            }
        )
    units.sort(key=lambda item: str(item["technical_id"]))
    return {
        "strategies": enriched_strategies,
        "units": units,
        "count": len(enriched_strategies),
        "generated_at": _iso_now(),
    }


@router.get("/ops/agents")
async def ops_agents() -> dict[str, object]:
    return await agents_fleet()


@router.get("/ops/directives")
async def ops_directives() -> dict[str, object]:
    return {
        "directives": [
            {"name": "halt_trading", "path": "/api/emergency/halt"},
            {"name": "resume_trading", "path": "/api/emergency/resume"},
            {"name": "run_executor", "path": "/api/executor/run"},
        ],
        "generated_at": _iso_now(),
    }


@router.get("/ops/judgments")
async def ops_judgments() -> dict[str, object]:
    outcomes = await _safe_recent_outcomes(limit=20)
    rows = [
        {
            "trade_id": item.trade_id,
            "signal_id": item.signal_id,
            "symbol": item.symbol,
            "outcome": item.outcome.value,
            "pnl_pct": item.pnl_pct,
        }
        for item in outcomes
    ]
    return {"judgments": rows, "count": len(rows), "generated_at": _iso_now()}


@router.get("/ops/governance")
async def ops_governance(container: Container = Depends(get_container)) -> dict[str, object]:
    return await governance_locks(container)


@router.get("/ops/build")
async def ops_build() -> dict[str, object]:
    return {
        "build": {
            "stack": "fastapi+tortoise+asyncio",
            "api_compat_mode": True,
            "timestamp": _iso_now(),
        }
    }


@router.get("/ops/state")
async def ops_state(container: Container = Depends(get_container)) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    return {
        "state": {
            "profiles_count": len(profiles),
            "queue": container.queue_bus.snapshot_sizes(),
            "trading_halted": container.trading_halted,
            "scheduler_jobs": _scheduler_job_count(container),
        },
        "generated_at": _iso_now(),
    }


@router.get("/ops/plans")
async def ops_plans() -> dict[str, object]:
    return {
        "plans": [
            {"id": "BEHAVIOR-01", "status": "in_progress", "scope": "monitor and control-plane formula parity"},
            {"id": "BEHAVIOR-02", "status": "pending", "scope": "scan and trigger engine wiring"},
            {"id": "BEHAVIOR-03", "status": "pending", "scope": "ml-risk-regime engine parity"},
        ],
        "generated_at": _iso_now(),
    }


@router.get("/monitor")
async def monitor_root(container: Container = Depends(get_container)) -> dict[str, object]:
    summary = await build_monitor_summary_payload(container)
    summary["status"] = "HALTED" if container.trading_halted else "OK"
    return summary


@router.get("/monitor/bot_improvement")
async def monitor_bot_improvement() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    strategies = await _safe_list_strategies()
    outcomes = await _safe_recent_outcome_rows(limit=500)
    signals = await _safe_recent_signal_rows(limit=500)
    signals_by_bot = _signals_grouped_by_bot(signals, profiles, strategies)
    outcomes_by_bot = _outcomes_grouped_by_bot(outcomes, profiles, strategies)

    rows: list[dict[str, object]] = []
    by_bot: dict[str, dict[str, object]] = {}
    for bot_id, profile in profiles.items():
        bot_outcomes = outcomes_by_bot.get(bot_id, [])
        bot_signals = signals_by_bot.get(bot_id, [])
        closed = [row for row in bot_outcomes if row.outcome != "open"]
        open_count = sum(1 for row in bot_outcomes if row.outcome == "open")
        wins = sum(1 for row in closed if row.outcome == "win")
        total_closed = len(closed)
        win_rate = round(wins / total_closed, 4) if total_closed > 0 else None
        confidence = _average_signal_score(bot_signals)
        quality_score = round((win_rate or 0.0) * 100, 2) if win_rate is not None else None
        row: dict[str, object] = {
            "bot_id": bot_id,
            "display_name": str(profile.get("display_name", bot_id)),
            "lifecycle_state": _lifecycle_state(profile),
            "enabled": bool(profile.get("enabled", False)),
            "autopilot_state": _autopilot_state(profile),
            "quality_score": quality_score,
            "confidence": confidence,
            "win_rate": win_rate,
            "total_trades": len(bot_outcomes),
            "closed_trades": total_closed,
            "open_trades": open_count,
            "suppressed": (not bool(profile.get("enabled", False))) or _lifecycle_state(profile) in {"offline", "retired"} or _autopilot_state(profile) == "shadow",
            "trend": _trend_from_outcomes(closed),
        }
        rows.append(row)
        by_bot[bot_id] = row
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {"bots": rows, "by_bot": by_bot, "count": len(rows), "generated_at": _iso_now()}


@router.get("/monitor/execution_quality")
async def monitor_execution_quality() -> dict[str, object]:
    profiles = await _safe_list_profiles()
    strategies = await _safe_list_strategies()
    orders = await _safe_recent_order_rows(limit=500)
    executions = await _safe_recent_execution_rows(limit=500)
    grouped_orders: dict[str, list[OrderRecord]] = defaultdict(list)
    grouped_executions: dict[str, list[ExecutionRecord]] = defaultdict(list)

    for order in orders:
        bot_id = _infer_bot_id(order.signal.signal_id if order.signal is not None else "", profiles, strategies)
        grouped_orders[bot_id].append(order)
    for execution in executions:
        signal_id = ""
        if execution.order is not None and execution.order.signal is not None:
            signal_id = execution.order.signal.signal_id
        bot_id = _infer_bot_id(signal_id, profiles, strategies)
        grouped_executions[bot_id].append(execution)

    rows: list[dict[str, object]] = []
    total_orders = len(orders)
    executed_orders = 0
    for bot_id, profile in profiles.items():
        bot_orders = grouped_orders.get(bot_id, [])
        bot_executions = grouped_executions.get(bot_id, [])
        fills = sum(1 for row in bot_orders if row.status == "executed")
        rejects = sum(1 for row in bot_orders if row.status in {"failed", "canceled"})
        executed_orders += fills
        latencies_ms: list[float] = []
        first_exec_by_order: dict[int, datetime] = {}
        for execution in bot_executions:
            order_id = execution.order.id if execution.order is not None else 0
            current = first_exec_by_order.get(order_id)
            if current is None or execution.created_at < current:
                first_exec_by_order[order_id] = execution.created_at
        for order in bot_orders:
            exec_at = first_exec_by_order.get(order.id)
            if exec_at is None or order.created_at is None:
                continue
            latency_ms = (exec_at - order.created_at).total_seconds() * 1000.0
            if 0.0 <= latency_ms <= 600000.0:
                latencies_ms.append(latency_ms)
        rows.append(
            {
                "bot_id": bot_id,
                "display_name": str(profile.get("display_name", bot_id)),
                "fills": fills,
                "rejects": rejects,
                "reject_rate": round(rejects / max(fills + rejects, 1), 3) if (fills + rejects) > 0 else None,
                "avg_latency_ms": round(sum(latencies_ms) / len(latencies_ms), 1) if latencies_ms else None,
                "last_broker_response": bot_executions[0].status if bot_executions else None,
                "last_response_at": bot_executions[0].created_at.isoformat() if bot_executions else None,
                "sample_size": len(bot_orders),
            }
        )
    rows.sort(key=lambda item: str(item["bot_id"]))
    return {
        "bots": rows,
        "count": len(rows),
        "total_outcomes": total_orders,
        "fill_rate": round(executed_orders / max(total_orders, 1), 4) if total_orders > 0 else 0.0,
        "generated_at": _iso_now(),
    }


@router.get("/monitor/signal_quality")
async def monitor_signal_quality(container: Container = Depends(get_container)) -> dict[str, object]:
    measured_error: str | None = None
    try:
        measured = await MeasurementService().signal_quality_report(window=1000)
    except Exception as exc:
        measured = {}
        measured_error = f"{type(exc).__name__}: {exc}"
        logger.exception("monitor.signal_quality measurement query failed")
    rows_error: str | None = None
    try:
        signals = await SignalRecord.all().order_by("-created_at").limit(1000)
    except Exception as exc:
        signals = []
        rows_error = f"{type(exc).__name__}: {exc}"
        logger.exception("monitor.signal_quality signals query failed")
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_source: dict[str, dict[str, object]] = {}
    now = datetime.now(timezone.utc)
    news_freshness_by_source = await _latest_news_freshness_by_source(limit=4000)
    scan_freshness_by_source = _latest_scheduler_scan_freshness_by_source(container)
    for signal in signals:
        source = str(signal.source or "unknown")
        row = by_source.setdefault(
            source,
            {
                "source": source,
                "total": 0,
                "today": 0,
                "pending": 0,
                "rejected": 0,
                "executed": 0,
                "traded": 0,
                "latest_ts": None,
                "avg_score": 0.0,
            },
        )
        row["total"] = _as_int(row.get("total")) + 1
        if signal.created_at.isoformat().startswith(today_prefix):
            row["today"] = _as_int(row.get("today")) + 1
        if signal.status == "pending":
            row["pending"] = _as_int(row.get("pending")) + 1
        elif signal.status == "rejected":
            row["rejected"] = _as_int(row.get("rejected")) + 1
        elif signal.status == "executed":
            row["executed"] = _as_int(row.get("executed")) + 1
            row["traded"] = _as_int(row.get("traded")) + 1
        if signal.score is not None:
            row["avg_score"] = _as_float(row.get("avg_score")) + float(signal.score)
        latest_ts = row["latest_ts"]
        created_at = signal.created_at.isoformat()
        if latest_ts is None or created_at > str(latest_ts):
            row["latest_ts"] = created_at

    rows: list[dict[str, object]] = []
    measured_by_source = {
        str(item.get("source")): item
        for item in _object_dict_rows(measured.get("sources"))
    }
    for row in by_source.values():
        total = _as_int(row.get("total"))
        latest_ts = row["latest_ts"]
        freshness_min: int | None = None
        freshness_origin = "signal"
        source_name = str(row.get("source", "unknown"))
        fetched_ts = news_freshness_by_source.get(source_name)
        if fetched_ts is not None:
            freshness_min = int((now - fetched_ts).total_seconds() / 60)
            freshness_origin = "news_fetch"
            row["latest_ts"] = fetched_ts.isoformat()
        else:
            scanned_ts, scan_origin = _scan_freshness_for_source(
                source_name=source_name,
                freshness_by_source=scan_freshness_by_source,
            )
            if scanned_ts is not None:
                freshness_min = int((now - scanned_ts).total_seconds() / 60)
                freshness_origin = scan_origin
                row["latest_ts"] = scanned_ts.isoformat()
        if freshness_min is None and isinstance(latest_ts, str):
            freshness_min = int((now - datetime.fromisoformat(latest_ts)).total_seconds() / 60)
        row["conversion_rate"] = round(_as_int(row.get("traded")) / max(total, 1), 3)
        row["avg_score"] = round(_as_float(row.get("avg_score")) / total, 4) if total > 0 else 0.0
        row["freshness_min"] = freshness_min
        row["freshness_origin"] = freshness_origin
        measured_row = measured_by_source.get(str(row["source"]))
        if measured_row is not None:
            row["approval_rate"] = measured_row.get("approval_rate")
            row["false_positive_rate"] = measured_row.get("false_positive_rate")
            row["signal_quality"] = measured_row.get("signal_quality")
            row["true_positives"] = measured_row.get("true_positives")
            row["false_positives"] = measured_row.get("false_positives")
        rows.append(row)
    rows.sort(key=lambda item: (-int(item["today"]), str(item["source"])))

    total_signals = len(signals)
    avg_score = round(sum(float(signal.score or 0.0) for signal in signals) / total_signals, 4) if total_signals > 0 else 0.0
    pending = sum(1 for signal in signals if signal.status == "pending")
    rejected = sum(1 for signal in signals if signal.status == "rejected")
    executed = sum(1 for signal in signals if signal.status == "executed")
    return {
        "sources": rows,
        "count": len(rows),
        "window_size": total_signals,
        "total_signals": total_signals,
        "avg_score": avg_score,
        "pending": pending,
        "rejected": rejected,
        "executed": executed,
        "formula_version": measured.get("formula_version", "signal_quality.compat"),
        "db_context_ready": container.db_context_ready,
        "degraded": measured_error is not None or rows_error is not None or not container.db_context_ready,
        "query_error": rows_error,
        "measurement_error": measured_error,
        "generated_at": _iso_now(),
    }


async def _latest_news_freshness_by_source(*, limit: int) -> dict[str, datetime]:
    try:
        async with UnitOfWork() as uow:
            rows = await AuditLogsRepository(connection=uow.connection).list_recent_by_prefix(
                prefix="news.article",
                limit=limit,
            )
    except Exception:
        return {}
    latest: dict[str, datetime] = {}
    for row in rows:
        payload = row.payload
        if not isinstance(payload, dict):
            continue
        source = payload.get("source")
        if not isinstance(source, str):
            continue
        normalized = source.strip()
        if not normalized:
            continue
        current = latest.get(normalized)
        if current is None or row.created_at > current:
            latest[normalized] = row.created_at
    return latest


def _latest_scheduler_scan_freshness_by_source(container: Container) -> dict[str, datetime]:
    scheduler = getattr(container, "scheduler", None)
    if scheduler is None:
        return {}

    snapshot = scheduler.snapshot()
    jobs = _object_list(snapshot.get("jobs"))
    latest: dict[str, datetime] = {}
    job_source_map: dict[str, tuple[str, ...]] = {
        "scanner.scout": (
            "scout",
            "watchlist_momentum",
            "macro_regime",
            "cross_asset",
            "scout_watchlist_momentum",
            "scout_macro_regime",
            "scout_cross_asset",
        ),
        "scanner.tradecopy": ("tradecopy", "13f"),
        "scanner.options": ("options", "options_flow"),
        "scanner.swingtrade": ("swingtrade",),
        "scanner.ausmine": ("ausmine", "nugget_permit"),
        "scanner.evo_catalyst": ("evo_catalyst", "evo"),
    }
    for job in jobs:
        if not isinstance(job, dict):
            continue
        aliases = job_source_map.get(str(job.get("name", "")))
        raw_ts = job.get("last_run_at")
        run_count = _as_int(job.get("run_count"))
        if not aliases or not isinstance(raw_ts, str) or run_count <= 0:
            continue
        try:
            parsed = datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        for alias in aliases:
            current = latest.get(alias)
            if current is None or parsed > current:
                latest[alias] = parsed
    return latest


def _scan_freshness_for_source(
    *,
    source_name: str,
    freshness_by_source: dict[str, datetime],
) -> tuple[datetime | None, str]:
    normalized = source_name.strip().lower()
    if not normalized:
        return None, "signal"

    direct = freshness_by_source.get(normalized)
    if direct is not None:
        return direct, "scan"

    if normalized.startswith("scout_"):
        scout_scan = freshness_by_source.get("scout")
        if scout_scan is not None:
            return scout_scan, "scan"

    if normalized.startswith("tradecopy"):
        tradecopy_scan = freshness_by_source.get("tradecopy")
        if tradecopy_scan is not None:
            return tradecopy_scan, "scan"

    if normalized.startswith("options"):
        options_scan = freshness_by_source.get("options")
        if options_scan is not None:
            return options_scan, "scan"

    if normalized.startswith("swingtrade"):
        swingtrade_scan = freshness_by_source.get("swingtrade")
        if swingtrade_scan is not None:
            return swingtrade_scan, "scan"

    if normalized.startswith("ausmine") or normalized.startswith("nugget"):
        ausmine_scan = freshness_by_source.get("ausmine")
        if ausmine_scan is not None:
            return ausmine_scan, "scan"

    if normalized.startswith("evo"):
        evo_scan = freshness_by_source.get("evo_catalyst")
        if evo_scan is not None:
            return evo_scan, "scan"

    return None, "signal"


@router.get("/monitor/broker_health")
async def monitor_broker_health(container: Container = Depends(get_container)) -> dict[str, object]:
    brokers = await _broker_rows(container)
    return {"brokers": brokers, "count": len(brokers), "generated_at": _iso_now()}


@router.get("/monitor/model_drift")
async def monitor_model_drift() -> dict[str, object]:
    strategies = await _safe_list_strategies()
    profiles = await _safe_list_profiles()
    outcomes = await _safe_recent_outcome_rows(limit=500)
    strategy_rows: list[dict[str, object]] = []
    all_scores: list[float] = []
    for strategy_id, strategy in strategies.items():
        bot_ids = [str(item) for item in strategy.get("bot_ids", []) if isinstance(item, str)]
        related_outcomes = [
            row
            for row in outcomes
            if _infer_bot_id(row.signal.signal_id if row.signal is not None else "", profiles, strategies) in bot_ids
        ]
        closed = [row for row in related_outcomes if row.outcome != "open"]
        open_count = sum(1 for row in related_outcomes if row.outcome == "open")
        recent_wr, historical_wr, drift_flag = _drift_analysis(closed)
        quality_score = round(recent_wr * 100, 2) if recent_wr is not None else None
        confidence = quality_score
        if quality_score is not None:
            all_scores.append(quality_score / 100.0)
        strategy_rows.append(
            {
                "strategy_id": strategy_id,
                "name": str(strategy.get("name", strategy_id)),
                "lifecycle_state": str(strategy.get("lifecycle_state", "unknown")),
                "version": str(strategy.get("version", "v1")),
                "linked_bots": bot_ids,
                "current_confidence": confidence,
                "quality_score": quality_score,
                "recent_win_rate": None if recent_wr is None else round(recent_wr * 100, 1),
                "historical_win_rate": None if historical_wr is None else round(historical_wr * 100, 1),
                "drift_flag": drift_flag,
                "trades": len(related_outcomes),
                "closed_trades": len(closed),
                "open_trades": open_count,
                "suppressed": str(strategy.get("lifecycle_state", "")).lower() in {"offline", "retired"},
            }
        )
    drift_std = _standard_deviation(all_scores)
    strategy_rows.sort(key=lambda item: str(item["strategy_id"]))
    return {
        "strategies": strategy_rows,
        "count": len(strategy_rows),
        "score_drift_std": round(drift_std, 6),
        "sample_size": len(all_scores),
        "generated_at": _iso_now(),
    }


@router.get("/monitor/actions")
async def monitor_actions(container: Container = Depends(get_container)) -> dict[str, object]:
    summary = await _monitor_actions_summary(container)
    actions = _object_dict_rows(summary.get("actions"))
    return {
        "actions": actions,
        "count": len(actions),
        "high_priority_count": _as_int(summary.get("high_priority_count")),
        "trading_halted": container.trading_halted,
        "generated_at": _iso_now(),
    }


@router.post("/monitor/scout_bridge")
async def monitor_scout_bridge(container: Container = Depends(get_container)) -> dict[str, object]:
    triggered_at = _iso_now()
    result = await ScanService().bridge_scout_to_signals(
        origin="api.monitor.scout_bridge",
        container=container,
    )
    return {
        "triggered": True,
        "persisted": True,
        "event_type": "MonitorScoutBridgeRequested",
        "bridged": _as_int(result.get("bridged")),
        "skipped": _as_int(result.get("skipped")),
        "sources": _object_dict(result.get("sources")),
        "errors": _object_list(result.get("errors")),
        "dispatch": _object_dict(result.get("dispatch")),
        "generated_at": triggered_at,
    }


@router.get("/monitor/strategy_shadow")
async def monitor_strategy_shadow() -> dict[str, object]:
    payload = await GovernanceService().strategy_shadow_report(persist=False)
    shadow_rows = [
        item
        for item in _object_dict_rows(payload.get("strategies"))
        if str(item.get("lifecycle_state", "")).lower() == "shadow"
    ]
    return {
        "strategies": shadow_rows,
        "count": len(shadow_rows),
        "signals_in_window": payload.get("signals_in_window", 0),
        "generated_at": _iso_now(),
    }


@router.get("/monitor/promotion_readiness")
async def monitor_promotion_readiness() -> dict[str, object]:
    payload = await GovernanceService().promotion_readiness_report(persist=False)
    return {
        "items": payload.get("items", []),
        "count": payload.get("count", 0),
        "generated_at": _iso_now(),
    }


async def build_monitor_summary_payload(container: Container) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    strategies = await _safe_list_strategies()
    control_plane: ControlPlaneSnapshot = await RuntimeControlPlaneService().snapshot(container)
    brokers = control_plane["brokers"]
    signal_quality = await monitor_signal_quality(container)
    model_drift = await monitor_model_drift()

    sources = _object_dict_rows(signal_quality.get("sources"))
    strategy_rows = _object_dict_rows(model_drift.get("strategies"))

    total_bots = len(profiles)
    active_bots = sum(1 for profile in profiles.values() if _lifecycle_state(profile) in {"paper", "live", "scaled"})
    enabled_bots = sum(1 for profile in profiles.values() if bool(profile.get("enabled", False)))
    connected_brokers = sum(1 for broker in brokers if bool(broker.get("connected")))
    total_brokers = len(brokers)
    signals_today = sum(_as_int(source.get("today")) for source in sources)
    stale_feeds = sum(
        1
        for source in sources
            if isinstance(source.get("freshness_min"), int) and _as_int(source.get("freshness_min")) > 120
    )
    drift_alerts = sum(
        1
        for strategy in strategy_rows
        if str(strategy.get("drift_flag")) in {"drift_down", "watch"}
    )
    pending = control_plane["db_truth"]["pending_signals"]
    issues = control_plane["high_priority_count"]
    if connected_brokers < total_brokers:
        issues += 1
    issues += stale_feeds
    issues += drift_alerts
    if enabled_bots == 0:
        issues += 1
    grade = "A" if issues == 0 else "B" if issues <= 1 else "C" if issues <= 3 else "D"

    return {
        "grade": grade,
        "total_bots": total_bots,
        "active_bots": active_bots,
        "enabled_bots": enabled_bots,
        "connected_brokers": connected_brokers,
        "total_brokers": total_brokers,
        "signals_today": signals_today,
        "trades_today": await _trades_today_count(),
        "drift_alerts": drift_alerts,
        "stale_feeds": stale_feeds,
        "issues": issues,
        "signals_pending": pending,
        "strategy_count": len(strategies),
        "queue": container.queue_bus.snapshot_sizes(),
        "scheduler": control_plane["scheduler"],
        "control_plane_status": control_plane["status"],
        "runtime_alerts": control_plane["alerts"],
        "generated_at": _iso_now(),
    }


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


async def _safe_pending_count() -> int:
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            return await repo.count_pending()
    except Exception:
        return 0


async def _safe_stale_pending_count() -> int:
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).replace(microsecond=0)
            return await repo.count_pending_older_than(cutoff)
    except Exception:
        return 0


async def _safe_recent_signals(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        logger.exception("control plane recent signals query failed")
        return []


async def _safe_recent_outcomes(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception:
        return []


async def _safe_recent_signal_rows(limit: int) -> list[SignalRecord]:
    try:
        rows = await SignalRecord.all().order_by("-created_at").limit(limit)
        return list(rows)
    except Exception:
        logger.exception("control plane signal rows query failed")
        return []


async def _safe_recent_outcome_rows(limit: int) -> list[TradeOutcomeRecord]:
    try:
        rows = await TradeOutcomeRecord.all().prefetch_related("signal").order_by("-created_at").limit(limit)
        return list(rows)
    except Exception:
        return []


async def _safe_recent_order_rows(limit: int) -> list[OrderRecord]:
    try:
        rows = await OrderRecord.all().select_related("signal").order_by("-created_at").limit(limit)
        return list(rows)
    except Exception:
        return []


async def _safe_recent_execution_rows(limit: int) -> list[ExecutionRecord]:
    try:
        rows = await ExecutionRecord.all().select_related("order__signal").order_by("-created_at").limit(limit)
        return list(rows)
    except Exception:
        return []


async def _latest_activity_at() -> str | None:
    timestamps: list[str] = []
    try:
        signal = await SignalRecord.all().order_by("-created_at").first()
        if signal is not None:
            timestamps.append(signal.created_at.isoformat())
        outcome = await TradeOutcomeRecord.all().order_by("-created_at").first()
        if outcome is not None:
            timestamps.append(outcome.created_at.isoformat())
    except Exception:
        return None
    if not timestamps:
        return None
    return max(timestamps)


async def _broker_rows(container: Container) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in container.broker_router.list_brokers():
        broker = container.broker_router.get(name)
        if broker is None:
            continue
        connected = True
        details: dict[str, object]
        try:
            account = await broker.get_account()
            details = dict(account)
            connected = bool(details.get("connected", not bool(details.get("error"))))
        except Exception as exc:
            connected = False
            details = {"error": str(exc)[:200]}
        rows.append(
            {
                "broker": name,
                "name": name,
                "connected": connected,
                "status": "connected" if connected else "error",
                "mode": str(container.broker_router.default_broker_name),
                "market": _profile_market({}),
                "equity": _as_optional_float(details.get("equity")),
                "cash": _as_optional_float(details.get("cash")),
                "currency": str(details.get("currency", "USD")),
                "operating_capital": _as_optional_float(details.get("buying_power")),
                "is_capped": False,
                "data_status": "ok" if connected else "error",
                "last_seen": _iso_now(),
                "details": details,
            }
        )
    return rows


async def _safe_broker_positions(container: Container) -> list[dict[str, object]]:
    try:
        positions = await container.broker.get_positions()
    except Exception:
        return []
    return [dict(item) for item in positions]


async def _trades_today_count() -> int:
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rows = await TradeOutcomeRecord.filter(created_at__gte=datetime.fromisoformat(f"{today_prefix}T00:00:00+00:00")).count()
        return int(rows)
    except Exception:
        return 0


async def _monitor_actions_summary(container: Container) -> dict[str, object]:
    profiles = await _safe_list_profiles()
    model_drift = await monitor_model_drift()
    signal_quality = await monitor_signal_quality(container)
    broker_health = await monitor_broker_health(container)
    actions: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(action_type: str, issue: str, detail: str, priority: str) -> None:
        key = (action_type, issue)
        if key in seen or len(actions) >= 5:
            return
        seen.add(key)
        actions.append({"type": action_type, "issue": issue, "detail": detail, "priority": priority})

    enabled = sum(1 for profile in profiles.values() if bool(profile.get("enabled", False)))
    if enabled == 0:
        add("system", "no_enabled_bots", f"0/{len(profiles)} bots enabled", "high")

    strategy_rows = _object_dict_rows(model_drift.get("strategies"))
    for strategy in strategy_rows:
        drift_flag = str(strategy.get("drift_flag", ""))
        if drift_flag in {"drift_down", "watch"}:
            add("strategy", f"drift_{drift_flag}", str(strategy.get("name", strategy.get("strategy_id", "unknown"))), "high")

    source_rows = _object_dict_rows(signal_quality.get("sources"))
    stale = [
        row
        for row in source_rows
        if isinstance(row.get("freshness_min"), int) and _as_int(row.get("freshness_min")) > 120
    ]
    if stale:
        worst = max(stale, key=lambda item: _as_int(item.get("freshness_min")))
        add("signal", "stale_feed", f"{worst['source']} ({worst['freshness_min']}m)", "high")

    broker_rows = _object_dict_rows(broker_health.get("brokers"))
    for broker in broker_rows:
        if not bool(broker.get("connected")):
            add("broker", f"{broker['broker']}_disconnected", str(broker["broker"]).upper(), "high")

    zero_alloc = [
        str(profile.get("display_name", bot_id))
        for bot_id, profile in profiles.items()
        if _has_zero_allocation(profile) and _lifecycle_state(profile) not in {"offline", "retired"}
    ]
    if zero_alloc:
        detail = f"{len(zero_alloc)} bots ({zero_alloc[0]}...)" if len(zero_alloc) > 1 else zero_alloc[0]
        add("bot", "zero_allocation", detail, "medium")

    return {
        "actions": actions,
        "high_priority_count": sum(1 for item in actions if str(item["priority"]) == "high"),
    }


def _build_agent_row(bot_id: str, profile: dict[str, object]) -> dict[str, object]:
    runtime_state = _runtime_status_for_profile(profile)
    return {
        "technical_id": bot_id,
        "display_name": str(profile.get("display_name", bot_id)),
        "family": _profile_family(profile),
        "role": "trading",
        "schedule": "event-driven",
        "status": runtime_state,
        "lifecycle_state": _lifecycle_state(profile),
        "last_run_at": None,
        "last_severity": "s0",
        "finding_count": 0,
        "open_tasks": 0,
        "memory_status": "ok",
        "model_route": "asyncio",
    }


def _family_counts(profiles: dict[str, dict]) -> dict[str, int]:
    families: dict[str, int] = {}
    for profile in profiles.values():
        family = _profile_family(profile)
        families[family] = families.get(family, 0) + 1
    return families


def _profile_family(profile: dict[str, object]) -> str:
    return str(profile.get("strategy_type", "unknown")).lower()


def _profile_market(profile: dict[str, object]) -> str:
    return str(profile.get("market", "multi")).lower()


def _profile_broker(profile: dict[str, object]) -> str:
    return str(profile.get("broker", "paper")).lower()


def _lifecycle_state(profile: dict[str, object]) -> str:
    return str(profile.get("lifecycle_state", "unknown")).lower()


def _autopilot_state(profile: dict[str, object]) -> str:
    value = str(profile.get("autopilot_state", "active")).strip().lower()
    return "shadow" if value == "shadow" else "active"


def _runtime_status_for_profile(profile: dict[str, object]) -> str:
    lifecycle = _lifecycle_state(profile)
    enabled = bool(profile.get("enabled", False))
    if lifecycle in {"offline", "retired"}:
        return "offline"
    if _autopilot_state(profile) == "shadow":
        return "shadow"
    return "live" if enabled else "idle"


def _signals_grouped_by_bot(
    signals: list[SignalRecord],
    profiles: dict[str, dict],
    strategies: dict[str, dict],
) -> dict[str, list[SignalRecord]]:
    grouped: dict[str, list[SignalRecord]] = defaultdict(list)
    for signal in signals:
        bot_id = _infer_bot_id(signal.signal_id, profiles, strategies)
        grouped[bot_id].append(signal)
    return grouped


def _outcomes_grouped_by_bot(
    outcomes: list[TradeOutcomeRecord],
    profiles: dict[str, dict],
    strategies: dict[str, dict],
) -> dict[str, list[TradeOutcomeRecord]]:
    grouped: dict[str, list[TradeOutcomeRecord]] = defaultdict(list)
    for outcome in outcomes:
        signal_id = outcome.signal.signal_id if outcome.signal is not None else ""
        bot_id = _infer_bot_id(signal_id, profiles, strategies)
        grouped[bot_id].append(outcome)
    return grouped


def _infer_bot_id(signal_id: str, profiles: dict[str, dict], strategies: dict[str, dict]) -> str:
    lower = signal_id.lower()
    for bot_id in profiles:
        normalized = bot_id.replace("_bot", "")
        if bot_id in lower or normalized in lower:
            return bot_id
    for strategy in strategies.values():
        bot_ids = strategy.get("bot_ids")
        if not isinstance(bot_ids, list):
            continue
        for bot_id in bot_ids:
            if isinstance(bot_id, str) and bot_id.lower() in lower:
                return bot_id
    if "copy" in lower:
        return "copycat"
    if "option" in lower:
        return "gambler"
    if "swing" in lower or "drift" in lower:
        return "drifter"
    if "mine" in lower or "nugget" in lower or "aus" in lower:
        return "nugget_bot"
    if "turbo" in lower:
        return "turbo"
    return "unknown"


def _average_signal_score(signals: list[SignalRecord]) -> float | None:
    scores = [float(signal.score) for signal in signals if signal.score is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


def _trend_from_outcomes(outcomes: list[TradeOutcomeRecord]) -> str:
    if len(outcomes) < 6:
        return "flat"
    half = len(outcomes) // 2
    recent = outcomes[:half]
    older = outcomes[half:]
    recent_wins = sum(1 for row in recent if row.outcome == "win")
    older_wins = sum(1 for row in older if row.outcome == "win")
    recent_wr = recent_wins / max(len(recent), 1)
    older_wr = older_wins / max(len(older), 1)
    delta = recent_wr - older_wr
    if delta > 0.10:
        return "up"
    if delta < -0.10:
        return "down"
    return "flat"


def _drift_analysis(outcomes: list[TradeOutcomeRecord]) -> tuple[float | None, float | None, str]:
    if not outcomes:
        return None, None, "no_data"
    if len(outcomes) < 5:
        wins = sum(1 for row in outcomes if row.outcome == "win")
        return wins / len(outcomes), None, "insufficient_data"
    half = len(outcomes) // 2
    recent = outcomes[:half]
    older = outcomes[half:]
    recent_wr = sum(1 for row in recent if row.outcome == "win") / max(len(recent), 1)
    older_wr = sum(1 for row in older if row.outcome == "win") / max(len(older), 1)
    delta = recent_wr - older_wr
    if delta < -0.15:
        drift_flag = "drift_down"
    elif delta < -0.08:
        drift_flag = "watch"
    elif delta > 0.15:
        drift_flag = "improving"
    else:
        drift_flag = "stable"
    return recent_wr, older_wr, drift_flag


def _standard_deviation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return variance ** 0.5


def _has_zero_allocation(profile: dict[str, object]) -> bool:
    allocation = profile.get("allocation")
    if not isinstance(allocation, dict):
        return True
    usd = _as_optional_float(allocation.get("usd")) or 0.0
    aud = _as_optional_float(allocation.get("aud")) or 0.0
    capital_pct = _as_optional_float(allocation.get("capital_pct")) or 0.0
    return usd == 0.0 and aud == 0.0 and capital_pct == 0.0


def _scheduler_snapshot(container: Container) -> SchedulerSnapshot:
    if container.scheduler is None:
        return SchedulerSnapshot(enabled=False, active=False, job_count=0, jobs=[])
    return container.scheduler.snapshot()


def _worker_snapshot(container: Container) -> ProcessManagerSnapshot:
    if container.process_manager is None:
        return ProcessManagerSnapshot(
            running=False,
            io_worker_target=0,
            active_workers=0,
            workers=[],
            cpu_pool={"max_workers": 0},
        )
    return container.process_manager.snapshot()


def _scheduler_job_count(container: Container) -> int:
    snapshot = _scheduler_snapshot(container)
    return snapshot["job_count"]
