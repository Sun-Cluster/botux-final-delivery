from __future__ import annotations

from typing import NotRequired, TypedDict

from domain.enums import DutySeverity, ReconcileStatus, RuntimeHealthStatus


class WorkerRuntimeSnapshot(TypedDict):
    active: bool
    registered_handlers: list[str]
    processed_count: int
    retry_count: int
    dead_letter_count: int
    error_count: int
    dedup_skip_count: int
    unknown_type_count: int


class CpuPoolSnapshot(TypedDict):
    max_workers: int


class ProcessManagerSnapshot(TypedDict):
    running: bool
    io_worker_target: int
    active_workers: int
    workers: list[WorkerRuntimeSnapshot]
    cpu_pool: CpuPoolSnapshot


class SchedulerJobSnapshot(TypedDict):
    name: str
    interval_seconds: int
    run_on_start: bool
    run_count: int
    last_run_at: str | None
    last_error: str | None
    active: bool


class SchedulerSnapshot(TypedDict):
    enabled: bool
    active: bool
    job_count: int
    jobs: list[SchedulerJobSnapshot]


class BrokerRow(TypedDict):
    broker: str
    connected: bool
    mode: str
    equity: float | None
    cash: float | None
    buying_power: float | None
    currency: str
    last_seen: str
    details: dict[str, object]


class DbTruthSnapshot(TypedDict):
    pending_signals: int
    stale_pending_15m: int
    stale_pending_24h: int
    open_outcomes: int
    closed_outcomes_today: int
    latest_signal_at: str | None
    latest_outcome_at: str | None
    latest_snapshot_at: str | None


class ReconcileSnapshot(TypedDict):
    status: ReconcileStatus
    last_run: str | None
    issue_count: int
    issues: list[str]
    stale: bool
    age_seconds: NotRequired[float]


class DutyRow(TypedDict):
    duty_id: str
    status: RuntimeHealthStatus
    severity: DutySeverity
    message: str
    checked_at: str
    evidence: dict[str, object]


class DutyCounts(TypedDict):
    total: int
    healthy: int
    degraded: int
    blocked: int


class ControlPlaneSnapshot(TypedDict):
    status: RuntimeHealthStatus
    grade: str
    checked_at: str
    event_loop_latency_ms: float
    queue: dict[str, int]
    workers: ProcessManagerSnapshot
    scheduler: SchedulerSnapshot
    brokers: list[BrokerRow]
    duties: list[DutyRow]
    alerts: list[DutyRow]
    high_priority_count: int
    db_truth: DbTruthSnapshot
    reconcile: ReconcileSnapshot
    trading_halted: bool
    halt_reason: str | None
    duty_counts: DutyCounts
