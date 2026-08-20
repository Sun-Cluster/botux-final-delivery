from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.services.control_plane.types import (
    BrokerRow,
    ControlPlaneSnapshot,
    CpuPoolSnapshot,
    DbTruthSnapshot,
    DutyCounts,
    DutyRow,
    ProcessManagerSnapshot,
    ReconcileSnapshot,
    SchedulerJobSnapshot,
    SchedulerSnapshot,
)
from app.services.runtime_config.service import RuntimeConfigService
from db.repositories.positions_repo import PositionSnapshotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import DutySeverity, ReconcileStatus, RuntimeHealthStatus
from runtime.health import event_loop_latency_ms

if TYPE_CHECKING:
    from runtime.container import Container


@dataclass(frozen=True)
class _DbRuntimeState:
    pending_signals: int
    stale_pending_15m: int
    stale_pending_24h: int
    open_outcomes: int
    closed_outcomes_today: int
    latest_signal_at: str | None
    latest_outcome_at: str | None
    latest_snapshot_at: str | None


class RuntimeControlPlaneService:
    async def snapshot(self, container: "Container") -> ControlPlaneSnapshot:
        checked_at = datetime.now(timezone.utc)
        queue_snapshot = container.queue_bus.snapshot_sizes()
        worker_snapshot = _worker_snapshot(container)
        scheduler_snapshot = _scheduler_snapshot(container)
        brokers = await _broker_rows(container)
        db_state = await _load_db_state()
        reconcile_state = await _reconcile_state(container, checked_at=checked_at)
        latency_ms = await event_loop_latency_ms(sample_seconds=0.01)
        duties = _build_duties(
            container=container,
            checked_at=checked_at,
            queue_snapshot=queue_snapshot,
            worker_snapshot=worker_snapshot,
            scheduler_snapshot=scheduler_snapshot,
            brokers=brokers,
            db_state=db_state,
            reconcile_state=reconcile_state,
            latency_ms=latency_ms,
        )
        alerts = [item for item in duties if item["status"] != RuntimeHealthStatus.HEALTHY]
        high_priority = sum(1 for item in alerts if item["severity"] == DutySeverity.HIGH)
        blocked = sum(1 for item in duties if item["status"] == RuntimeHealthStatus.BLOCKED)
        degraded = sum(1 for item in duties if item["status"] == RuntimeHealthStatus.DEGRADED)
        status = (
            RuntimeHealthStatus.BLOCKED
            if container.trading_halted or blocked > 0
            else RuntimeHealthStatus.DEGRADED
            if alerts
            else RuntimeHealthStatus.HEALTHY
        )
        grade = "A" if not alerts else "B" if high_priority == 0 else "C" if high_priority == 1 else "D"
        return {
            "status": status,
            "grade": grade,
            "checked_at": checked_at.isoformat(),
            "event_loop_latency_ms": round(latency_ms, 4),
            "queue": queue_snapshot,
            "workers": worker_snapshot,
            "scheduler": scheduler_snapshot,
            "brokers": brokers,
            "duties": duties,
            "alerts": alerts,
            "high_priority_count": high_priority,
            "db_truth": DbTruthSnapshot(
                pending_signals=db_state.pending_signals,
                stale_pending_15m=db_state.stale_pending_15m,
                stale_pending_24h=db_state.stale_pending_24h,
                open_outcomes=db_state.open_outcomes,
                closed_outcomes_today=db_state.closed_outcomes_today,
                latest_signal_at=db_state.latest_signal_at,
                latest_outcome_at=db_state.latest_outcome_at,
                latest_snapshot_at=db_state.latest_snapshot_at,
            ),
            "reconcile": reconcile_state,
            "trading_halted": container.trading_halted,
            "halt_reason": container.trading_halt_reason,
            "duty_counts": DutyCounts(
                total=len(duties),
                healthy=sum(1 for item in duties if item["status"] == RuntimeHealthStatus.HEALTHY),
                degraded=degraded,
                blocked=blocked,
            ),
        }


async def _load_db_state() -> _DbRuntimeState:
    checked_at = datetime.now(timezone.utc)
    stale_15m_cutoff = checked_at - timedelta(minutes=15)
    stale_24h_cutoff = checked_at - timedelta(hours=24)
    start_of_day = datetime(checked_at.year, checked_at.month, checked_at.day, tzinfo=timezone.utc)
    try:
        async with UnitOfWork() as uow:
            signals_repo = SignalsRepository(connection=uow.connection)
            outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
            snapshots_repo = PositionSnapshotsRepository(connection=uow.connection)
            pending_signals = await signals_repo.count_pending()
            stale_pending_15m = await signals_repo.count_pending_older_than(stale_15m_cutoff)
            stale_pending_24h = await signals_repo.count_pending_older_than(stale_24h_cutoff)
            open_outcomes = await outcomes_repo.count_open()
            closed_outcomes_today = await outcomes_repo.count_closed_since(start_of_day)
            recent_signals = await signals_repo.list_recent(limit=1)
            recent_outcomes = await outcomes_repo.list_recent(limit=1)
            recent_snapshots = await snapshots_repo.list_recent(limit=1)
    except Exception:
        return _DbRuntimeState(
            pending_signals=0,
            stale_pending_15m=0,
            stale_pending_24h=0,
            open_outcomes=0,
            closed_outcomes_today=0,
            latest_signal_at=None,
            latest_outcome_at=None,
            latest_snapshot_at=None,
        )
    latest_signal_at = recent_signals[0].created_at.isoformat() if recent_signals else None
    latest_outcome_at = recent_outcomes[0].opened_at.isoformat() if recent_outcomes else None
    latest_snapshot_at = None
    if recent_snapshots:
        raw_created_at = recent_snapshots[0].get("created_at")
        if isinstance(raw_created_at, str):
            latest_snapshot_at = raw_created_at
    return _DbRuntimeState(
        pending_signals=pending_signals,
        stale_pending_15m=stale_pending_15m,
        stale_pending_24h=stale_pending_24h,
        open_outcomes=open_outcomes,
        closed_outcomes_today=closed_outcomes_today,
        latest_signal_at=latest_signal_at,
        latest_outcome_at=latest_outcome_at,
        latest_snapshot_at=latest_snapshot_at,
    )


async def _broker_rows(container: "Container") -> list[BrokerRow]:
    rows: list[BrokerRow] = []
    for name in container.broker_router.list_brokers():
        broker = container.broker_router.get(name)
        if broker is None:
            continue
        connected = True
        details: dict[str, object]
        try:
            account = await broker.get_account()
            details = dict(account)
            if details.get("error"):
                connected = False
        except Exception as exc:
            connected = False
            details = {"error": str(exc)[:200]}
        rows.append(
            {
                "broker": name,
                "connected": connected,
                "mode": str(details.get("mode", container.broker_router.default_broker_name)),
                "equity": _as_float(details.get("equity")),
                "cash": _as_float(details.get("cash")),
                "buying_power": _as_float(details.get("buying_power")),
                "currency": str(details.get("currency", "USD")),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "details": details,
            }
        )
    return rows


async def _reconcile_state(container: "Container", *, checked_at: datetime) -> ReconcileSnapshot:
    runtime = RuntimeConfigService()
    reconcile_interval = await runtime.resolve("scheduler.reconcile_interval_seconds")
    configured_interval = max(int(reconcile_interval.value or 0), 0)
    if container.last_reconcile_report is None or container.last_reconcile_run_at is None:
        return {
            "status": ReconcileStatus.MISSING,
            "last_run": container.last_reconcile_run_at,
            "issue_count": 0,
            "issues": [],
            "stale": False,
            "configured_interval_seconds": configured_interval,
        }
    issues = container.last_reconcile_report.get("issues", [])
    issue_rows = [str(item) for item in issues] if isinstance(issues, list) else []
    try:
        last_run_dt = datetime.fromisoformat(container.last_reconcile_run_at.replace("Z", "+00:00"))
    except ValueError:
        last_run_dt = checked_at
    interval = max(configured_interval, 300)
    age_seconds = max((checked_at - last_run_dt).total_seconds(), 0.0)
    stale = age_seconds > (interval * 2)
    return {
        "status": ReconcileStatus.WARN if issue_rows or stale else ReconcileStatus.OK,
        "last_run": container.last_reconcile_run_at,
        "issue_count": len(issue_rows),
        "issues": issue_rows[:10],
        "stale": stale,
        "age_seconds": round(age_seconds, 3),
        "configured_interval_seconds": interval,
    }


def _build_duties(
    *,
    container: "Container",
    checked_at: datetime,
    queue_snapshot: dict[str, int],
    worker_snapshot: ProcessManagerSnapshot,
    scheduler_snapshot: SchedulerSnapshot,
    brokers: list[BrokerRow],
    db_state: _DbRuntimeState,
    reconcile_state: ReconcileSnapshot,
    latency_ms: float,
) -> list[DutyRow]:
    disconnected = [str(row["broker"]) for row in brokers if not bool(row.get("connected"))]
    broker_watchdog_status = RuntimeHealthStatus.HEALTHY if not disconnected else RuntimeHealthStatus.BLOCKED
    sentinel_running = worker_snapshot["running"] and worker_snapshot["active_workers"] > 0
    dead_letters = int(queue_snapshot.get("dead_letter_queue", 0))
    retry_queue = int(queue_snapshot.get("retry_queue", 0))
    work_queue = int(queue_snapshot.get("work_queue", 0))
    if not sentinel_running and (work_queue > 0 or retry_queue > 0):
        sentinel_status = RuntimeHealthStatus.BLOCKED
        sentinel_message = "queue backlog without active workers"
    elif dead_letters > 0 or latency_ms > 250.0:
        sentinel_status = RuntimeHealthStatus.DEGRADED
        sentinel_message = "runtime backpressure detected"
    else:
        sentinel_status = RuntimeHealthStatus.HEALTHY
        sentinel_message = "workers and queue responsive"

    if reconcile_state["status"] == ReconcileStatus.MISSING and int(reconcile_state.get("configured_interval_seconds", 0) or 0) > 0:
        portfolio_status = RuntimeHealthStatus.DEGRADED
        portfolio_message = "reconcile report missing"
    elif bool(reconcile_state.get("stale")):
        portfolio_status = RuntimeHealthStatus.DEGRADED
        portfolio_message = "reconcile report stale"
    elif reconcile_state["issue_count"] > 0:
        portfolio_status = RuntimeHealthStatus.DEGRADED
        portfolio_message = "reconcile mismatches detected"
    else:
        portfolio_status = RuntimeHealthStatus.HEALTHY
        portfolio_message = "reconcile truth current"

    if db_state.latest_signal_at is None:
        intel_status = RuntimeHealthStatus.DEGRADED
        intel_message = "no persisted signal activity"
    elif db_state.stale_pending_24h > 0:
        intel_status = RuntimeHealthStatus.DEGRADED
        intel_message = "stale pending signals detected"
    else:
        intel_status = RuntimeHealthStatus.HEALTHY
        intel_message = "signal flow active"

    if dead_letters > 0:
        hygiene_status = RuntimeHealthStatus.DEGRADED
        hygiene_message = "dead-letter queue has backlog"
    elif retry_queue > 10:
        hygiene_status = RuntimeHealthStatus.DEGRADED
        hygiene_message = "retry backlog elevated"
    else:
        hygiene_status = RuntimeHealthStatus.HEALTHY
        hygiene_message = "queue hygiene clear"

    if not scheduler_snapshot["enabled"]:
        scheduler_status = RuntimeHealthStatus.DEGRADED
        scheduler_message = "scheduler disabled"
    elif not scheduler_snapshot["active"] and scheduler_snapshot["job_count"] > 0:
        scheduler_status = RuntimeHealthStatus.DEGRADED
        scheduler_message = "scheduler configured but inactive"
    else:
        scheduler_status = RuntimeHealthStatus.HEALTHY
        scheduler_message = "scheduler active"

    return [
        _duty_row(
            duty_id="broker_watchdog",
            status=broker_watchdog_status,
            severity=DutySeverity.HIGH if broker_watchdog_status == RuntimeHealthStatus.BLOCKED else DutySeverity.INFO,
            message="all brokers connected" if not disconnected else f"disconnected: {', '.join(disconnected)}",
            checked_at=checked_at,
            evidence={"disconnected": disconnected, "brokers": len(brokers)},
        ),
        _duty_row(
            duty_id="sentinel",
            status=sentinel_status,
            severity=(
                DutySeverity.HIGH
                if sentinel_status == RuntimeHealthStatus.BLOCKED
                else DutySeverity.MEDIUM
                if sentinel_status == RuntimeHealthStatus.DEGRADED
                else DutySeverity.INFO
            ),
            message=sentinel_message,
            checked_at=checked_at,
            evidence={
                "running": sentinel_running,
                "work_queue": work_queue,
                "retry_queue": retry_queue,
                "dead_letter_queue": dead_letters,
                "latency_ms": round(latency_ms, 4),
            },
        ),
        _duty_row(
            duty_id="portfolio_guardian",
            status=portfolio_status,
            severity=DutySeverity.MEDIUM if portfolio_status != RuntimeHealthStatus.HEALTHY else DutySeverity.INFO,
            message=portfolio_message,
            checked_at=checked_at,
            evidence={
                "reconcile": reconcile_state,
                "open_outcomes": db_state.open_outcomes,
                "closed_outcomes_today": db_state.closed_outcomes_today,
            },
        ),
        _duty_row(
            duty_id="intel_pulse",
            status=intel_status,
            severity=DutySeverity.MEDIUM if intel_status != RuntimeHealthStatus.HEALTHY else DutySeverity.INFO,
            message=intel_message,
            checked_at=checked_at,
            evidence={
                "pending_signals": db_state.pending_signals,
                "stale_pending_15m": db_state.stale_pending_15m,
                "stale_pending_24h": db_state.stale_pending_24h,
                "latest_signal_at": db_state.latest_signal_at,
            },
        ),
        _duty_row(
            duty_id="scheduler_supervisor",
            status=scheduler_status,
            severity=DutySeverity.MEDIUM if scheduler_status != RuntimeHealthStatus.HEALTHY else DutySeverity.INFO,
            message=scheduler_message,
            checked_at=checked_at,
            evidence={"scheduler": scheduler_snapshot},
        ),
        _duty_row(
            duty_id="hygiene",
            status=hygiene_status,
            severity=DutySeverity.MEDIUM if hygiene_status != RuntimeHealthStatus.HEALTHY else DutySeverity.INFO,
            message=hygiene_message,
            checked_at=checked_at,
            evidence={
                "latest_snapshot_at": db_state.latest_snapshot_at,
                "dead_letter_queue": dead_letters,
                "retry_queue": retry_queue,
            },
        ),
    ]


def _duty_row(
    *,
    duty_id: str,
    status: RuntimeHealthStatus,
    severity: DutySeverity,
    message: str,
    checked_at: datetime,
    evidence: dict[str, object],
) -> DutyRow:
    return {
        "duty_id": duty_id,
        "status": status,
        "severity": severity,
        "message": message,
        "checked_at": checked_at.isoformat(),
        "evidence": evidence,
    }


def _scheduler_snapshot(container: "Container") -> SchedulerSnapshot:
    if container.scheduler is None:
        return SchedulerSnapshot(enabled=False, active=False, job_count=0, jobs=[])
    return container.scheduler.snapshot()


def _worker_snapshot(container: "Container") -> ProcessManagerSnapshot:
    if container.process_manager is None:
        return ProcessManagerSnapshot(
            running=False,
            io_worker_target=0,
            active_workers=0,
            workers=[],
            cpu_pool=CpuPoolSnapshot(max_workers=0),
        )
    return container.process_manager.snapshot()


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
