from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from app.services.order_status.reconcile import OrderStatusReconcileService
from app.services.outcome.service import OutcomeLifecycleService
from app.services.lanes.exit import LaneExitService
from app.services.autopilot.runtime_integration import AutopilotRuntimeIntegration
from app.usecases.process_pending_signals import process_pending_signals
from app.services.portfolio.service import PortfolioService
from app.services.reconcile.service import ReconcileService
from app.services.runtime.proof import RuntimeProofService
from app.services.runtime_config.service import RuntimeConfigService
from app.services.scan.service import ScanService
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from infra.queue.outbox_dispatcher import OutboxDispatcher
from db.uow import UnitOfWork
from runtime.logging import format_log_fields

pipeline_logger = logger.bind(pipeline_module=__name__)

if TYPE_CHECKING:
    from runtime.container import Container

JobCallable = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_seconds: int
    run: JobCallable
    run_on_start: bool = True


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    portfolio_snapshot_interval_seconds: int
    reconcile_interval_seconds: int
    news_scan_interval_seconds: int
    signal_broadcast_interval_seconds: int
    execution_loop_interval_seconds: int
    risk_cycle_interval_seconds: int
    position_monitor_interval_seconds: int
    scout_scan_interval_seconds: int
    tradecopy_scan_interval_seconds: int
    options_scan_interval_seconds: int
    swingtrade_scan_interval_seconds: int
    miner_scan_interval_seconds: int
    evo_scan_interval_seconds: int
    runtime_proof_interval_seconds: int
    autopilot_enabled: bool
    autopilot_interval_seconds: int


async def register_jobs(container: Container) -> list[ScheduledJob]:
    settings = await _load_scheduler_runtime_config()
    if settings.portfolio_snapshot_interval_seconds <= 0:
        logger.warning(
            "portfolio snapshot scheduler disabled (scheduler.portfolio_snapshot_interval_seconds<=0); "
            "DB-only positions/trades surfaces may remain stale"
        )

    jobs: list[ScheduledJob] = []
    outbox_dispatcher = OutboxDispatcher(container.queue_bus)
    scan_service = ScanService()
    logger.info("scheduler job registration started")

    async def outbox_dispatch_job() -> None:
        stats = await outbox_dispatcher.dispatch_pending(limit=100)
        await _emit_scheduler_event("OutboxDispatchTick", {"source": "scheduler.outbox", **stats})

    _add_job(
        jobs=jobs,
        job=ScheduledJob(
            name="runtime.outbox_dispatch",
            interval_seconds=max(settings.execution_loop_interval_seconds, 30),
            run=outbox_dispatch_job,
            run_on_start=False,
        ),
    )

    async def broker_watchdog_job() -> None:
        checks: list[dict[str, object]] = []
        for broker_name in container.broker_router.list_brokers():
            broker = container.broker_router.get(broker_name)
            if broker is None:
                continue
            had_exception = False
            try:
                account = await broker.get_account()
                connected = bool(account.get("connected", not bool(account.get("error"))))
                error = account.get("error")
            except Exception as exc:
                connected = False
                error = str(exc)[:300]
                had_exception = True
            checks.append({"broker": broker_name, "connected": connected, "error": error})
            if not connected:
                level = "ERROR" if had_exception else "WARNING"
                pipeline_logger.log(level, "pipeline.{} {}", "broker.watchdog_tick", format_log_fields({"broker": broker_name, "connected": connected, "error": error}))

        await _emit_scheduler_event("BrokerWatchdogTick", {"source": "scheduler.broker_watchdog", "checks": checks})

    _add_job(
        jobs=jobs,
        job=ScheduledJob(
            name="runtime.broker_watchdog",
            interval_seconds=10,
            run=broker_watchdog_job,
            run_on_start=True,
        ),
    )

    if settings.portfolio_snapshot_interval_seconds > 0:
        portfolio_service = PortfolioService(broker=container.broker)

        async def snapshot_job() -> None:
            await portfolio_service.snapshot()

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="portfolio.snapshot",
                interval_seconds=settings.portfolio_snapshot_interval_seconds,
                run=snapshot_job,
                run_on_start=True,
            ),
        )

    if settings.reconcile_interval_seconds > 0:
        reconcile_service = ReconcileService(broker=container.broker)

        async def reconcile_job() -> None:
            report = await reconcile_service.run()
            container.last_reconcile_report = report
            timestamp = report.get("timestamp")
            if isinstance(timestamp, str):
                container.last_reconcile_run_at = timestamp
            else:
                container.last_reconcile_run_at = None

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="reconcile.run",
                interval_seconds=settings.reconcile_interval_seconds,
                run=reconcile_job,
                run_on_start=False,
            ),
        )

    if settings.news_scan_interval_seconds > 0:

        async def news_scan_job() -> None:
            result = await scan_service.run_news_scan(container, origin="scheduler.news_scan")
            await _emit_scheduler_event("NewsScanTick", {"source": "scheduler.news_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.news",
                interval_seconds=settings.news_scan_interval_seconds,
                run=news_scan_job,
                run_on_start=True,
            ),
        )

    if settings.signal_broadcast_interval_seconds > 0:

        async def signal_broadcast_job() -> None:
            pending = await _safe_count_pending_signals()
            await _emit_scheduler_event(
                "SignalBroadcastTick",
                {"source": "scheduler.signal_broadcast", "pending_signals": pending},
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="execution.signal_broadcast",
                interval_seconds=settings.signal_broadcast_interval_seconds,
                run=signal_broadcast_job,
                run_on_start=False,
            ),
        )

    if settings.execution_loop_interval_seconds > 0:
        order_status_reconcile_service = OrderStatusReconcileService(container.broker_router)

        async def execution_loop_job() -> None:
            if container.trading_halted:
                await _emit_scheduler_event(
                    "ExecutionLoopSkipped",
                    {"reason": "trading_halted", "source": "scheduler.execution_loop"},
                )
                return
            result = await process_pending_signals(
                limit=25,
                quantity=1.0,
                process_manager=container.process_manager,
            )
            await _emit_scheduler_event(
                "ExecutionLoopTick",
                {
                    "source": "scheduler.execution_loop",
                    "processed": int(result.get("processed", 0)),
                    "enqueued": int(result.get("enqueued", 0)),
                    "rejected": int(result.get("rejected", 0)),
                    "executed": int(result.get("executed", 0)),
                    "errors": int(result.get("errors", 0)),
                },
            )

        async def order_status_sync_job() -> None:
            result = await order_status_reconcile_service.reconcile_active_orders(limit=100)
            if settings.portfolio_snapshot_interval_seconds <= 0:
                try:
                    snapshot_payload = await PortfolioService(broker=container.broker).snapshot()
                    result["snapshot_position_count"] = int(snapshot_payload.get("position_count", 0))
                    result["snapshot_source"] = str(snapshot_payload.get("source", "broker"))
                except Exception as exc:
                    result["snapshot_error"] = str(exc)[:200]
            await _emit_scheduler_event(
                "OrderStatusSyncTick",
                {"source": "scheduler.order_status_sync", **result},
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="execution.loop",
                interval_seconds=settings.execution_loop_interval_seconds,
                run=execution_loop_job,
                run_on_start=False,
            ),
        )
        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="runtime.order_status_sync",
                interval_seconds=max(10, min(settings.execution_loop_interval_seconds, 30)),
                run=order_status_sync_job,
                run_on_start=False,
            ),
        )

    if settings.risk_cycle_interval_seconds > 0:

        async def risk_cycle_job() -> None:
            outcomes = await _safe_recent_outcomes(limit=200)
            total = len(outcomes)
            wins = sum(1 for item in outcomes if item.outcome.value == "win")
            losses = sum(1 for item in outcomes if item.outcome.value == "loss")
            await _emit_scheduler_event(
                "RiskCycleTick",
                {
                    "source": "scheduler.risk_cycle",
                    "total_outcomes": total,
                    "wins": wins,
                    "losses": losses,
                    "trading_halted": container.trading_halted,
                },
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="risk.cycle",
                interval_seconds=settings.risk_cycle_interval_seconds,
                run=risk_cycle_job,
                run_on_start=False,
            ),
        )

    if settings.position_monitor_interval_seconds > 0:
        lane_exit_service = LaneExitService()

        async def position_monitor_job() -> None:
            positions = await container.broker.get_positions()
            account = await container.broker.get_account()
            lifecycle = await OutcomeLifecycleService(broker=container.broker).reconcile_open_outcomes()
            await _emit_scheduler_event(
                "PositionMonitorTick",
                {
                    "source": "scheduler.position_monitor",
                    "positions_count": len(positions),
                    "equity": account.get("equity"),
                    "cash": account.get("cash"),
                    "outcomes_checked": lifecycle.get("checked"),
                    "outcomes_closed": lifecycle.get("closed_count"),
                },
            )

        async def tradecopy_exit_job() -> None:
            result = await lane_exit_service.run_tradecopy_exits(container=container)
            await _emit_scheduler_event(
                "TradecopyExitTick",
                {"source": "scheduler.tradecopy_exit", **result},
            )

        async def options_exit_job() -> None:
            result = await lane_exit_service.run_options_exits(container=container)
            await _emit_scheduler_event(
                "OptionsExitTick",
                {"source": "scheduler.options_exit", **result},
            )

        async def swingtrade_exit_job() -> None:
            result = await lane_exit_service.run_swingtrade_exits(container=container)
            await _emit_scheduler_event(
                "SwingtradeExitTick",
                {"source": "scheduler.swingtrade_exit", **result},
            )

        async def evo_catalyst_exit_job() -> None:
            result = await lane_exit_service.run_evo_catalyst_exits(container=container)
            await _emit_scheduler_event(
                "EvoCatalystExitTick",
                {"source": "scheduler.evo_catalyst_exit", **result},
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="positions.monitor",
                interval_seconds=settings.position_monitor_interval_seconds,
                run=position_monitor_job,
                run_on_start=False,
            ),
        )
        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="exits.tradecopy",
                interval_seconds=settings.position_monitor_interval_seconds,
                run=tradecopy_exit_job,
                run_on_start=False,
            ),
        )
        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="exits.options",
                interval_seconds=settings.position_monitor_interval_seconds,
                run=options_exit_job,
                run_on_start=False,
            ),
        )
        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="exits.swingtrade",
                interval_seconds=settings.position_monitor_interval_seconds,
                run=swingtrade_exit_job,
                run_on_start=False,
            ),
        )
        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="exits.evo_catalyst",
                interval_seconds=settings.position_monitor_interval_seconds,
                run=evo_catalyst_exit_job,
                run_on_start=False,
            ),
        )

    if settings.scout_scan_interval_seconds > 0:

        async def scout_scan_job() -> None:
            result = await scan_service.run_scout_scan(container, origin="scheduler.scout_scan")
            await _emit_scheduler_event("ScoutScanTick", {"source": "scheduler.scout_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.scout",
                interval_seconds=settings.scout_scan_interval_seconds,
                run=scout_scan_job,
                run_on_start=True,
            ),
        )

    if settings.tradecopy_scan_interval_seconds > 0:

        async def tradecopy_scan_job() -> None:
            result = await scan_service.run_lane_scan(
                lane="tradecopy",
                container=container,
                origin="scheduler.tradecopy_scan",
            )
            await _emit_scheduler_event("TradecopyScanTick", {"source": "scheduler.tradecopy_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.tradecopy",
                interval_seconds=settings.tradecopy_scan_interval_seconds,
                run=tradecopy_scan_job,
                run_on_start=True,
            ),
        )

    if settings.options_scan_interval_seconds > 0:

        async def options_scan_job() -> None:
            result = await scan_service.run_lane_scan(
                lane="options",
                container=container,
                origin="scheduler.options_scan",
            )
            await _emit_scheduler_event("OptionsScanTick", {"source": "scheduler.options_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.options",
                interval_seconds=settings.options_scan_interval_seconds,
                run=options_scan_job,
                run_on_start=True,
            ),
        )

    if settings.swingtrade_scan_interval_seconds > 0:

        async def swingtrade_scan_job() -> None:
            result = await scan_service.run_lane_scan(
                lane="swingtrade",
                container=container,
                origin="scheduler.swingtrade_scan",
            )
            await _emit_scheduler_event("SwingtradeScanTick", {"source": "scheduler.swingtrade_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.swingtrade",
                interval_seconds=settings.swingtrade_scan_interval_seconds,
                run=swingtrade_scan_job,
                run_on_start=True,
            ),
        )

    if settings.miner_scan_interval_seconds > 0:

        async def miner_scan_job() -> None:
            result = await scan_service.run_lane_scan(
                lane="ausmine",
                container=container,
                origin="scheduler.miner_scan",
            )
            await _emit_scheduler_event("MinerScanTick", {"source": "scheduler.miner_scan", **result})

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.ausmine",
                interval_seconds=settings.miner_scan_interval_seconds,
                run=miner_scan_job,
                run_on_start=True,
            ),
        )

    if settings.evo_scan_interval_seconds > 0:

        async def evo_catalyst_scan_job() -> None:
            result = await scan_service.run_lane_scan(
                lane="evo_catalyst",
                container=container,
                origin="scheduler.evo_catalyst_scan",
            )
            await _emit_scheduler_event(
                "EvoCatalystScanTick",
                {"source": "scheduler.evo_catalyst_scan", **result},
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="scanner.evo_catalyst",
                interval_seconds=settings.evo_scan_interval_seconds,
                run=evo_catalyst_scan_job,
                run_on_start=True,
            ),
        )

    if settings.runtime_proof_interval_seconds > 0:
        runtime_proof_service = RuntimeProofService()

        async def runtime_proof_pack_job() -> None:
            payload = await runtime_proof_service.build_runtime_pack(
                container=container,
                window_minutes=max(15, settings.position_monitor_interval_seconds * 2),
            )
            await runtime_proof_service.persist_runtime_pack(payload=payload)
            await _emit_scheduler_event(
                "RuntimeProofPackTick",
                {
                    "source": "scheduler.runtime_proof_pack",
                    "window_minutes": payload.get("window_minutes"),
                    "open_positions": _nested_int(payload, "exits", "open_positions"),
                    "recent_exit_signals": _nested_int(payload, "exits", "recent_exit_signals"),
                    "recent_exit_orders": _nested_int(payload, "exits", "recent_exit_orders"),
                    "recent_outcomes_closed": _nested_int(payload, "exits", "recent_outcomes_closed"),
                    "unresolved_symbols": _nested_list(payload, "exits", "unresolved_symbols"),
                },
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="runtime.proof_pack",
                interval_seconds=settings.runtime_proof_interval_seconds,
                run=runtime_proof_pack_job,
                run_on_start=False,
            ),
        )

    if settings.autopilot_enabled and settings.autopilot_interval_seconds > 0:
        autopilot_runtime = AutopilotRuntimeIntegration()

        async def autopilot_evaluate_job() -> None:
            result = await autopilot_runtime.run_cycle(container=container)
            await _emit_scheduler_event(
                "AutopilotEvaluateTick",
                {"source": "scheduler.autopilot_evaluate", **result},
            )

        _add_job(
            jobs=jobs,
            job=ScheduledJob(
                name="autopilot.evaluate",
                interval_seconds=settings.autopilot_interval_seconds,
                run=autopilot_evaluate_job,
                run_on_start=False,
            ),
        )

    logger.info("scheduler job registration completed job_count={}", len(jobs))
    return jobs


async def _emit_scheduler_event(event_type: str, payload: dict[str, object]) -> None:
    payload_with_time = {
        **payload,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized_payload = _coerce_json_dict(payload_with_time)
    event_key = normalized_payload.get("generated_at")
    entity_key = str(event_key) if isinstance(event_key, str) else event_type.lower()
    try:
        async with UnitOfWork() as uow:
            await append_outbox_event(
                event_type=event_type,
                entity_key=entity_key,
                payload=normalized_payload,
                connection=uow.connection,
            )
    except Exception as exc:
        logger.warning("scheduler event emit skipped event_type={} error={}", event_type, str(exc)[:200])


async def _safe_count_pending_signals() -> int:
    try:
        async with UnitOfWork() as uow:
            repo = SignalsRepository(connection=uow.connection)
            return await repo.count_pending()
    except Exception as exc:
        logger.error("scheduler count pending signals failed: {}", str(exc)[:500])
        return 0


async def _safe_recent_outcomes(limit: int):
    try:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            return await repo.list_recent(limit=limit)
    except Exception as exc:
        logger.error("scheduler load recent outcomes failed: {}", str(exc)[:500])
        return []


async def _load_scheduler_runtime_config() -> SchedulerRuntimeConfig:
    runtime = RuntimeConfigService()
    return SchedulerRuntimeConfig(
        portfolio_snapshot_interval_seconds=max(0, int((await runtime.resolve("scheduler.portfolio_snapshot_interval_seconds")).value)),
        reconcile_interval_seconds=max(0, int((await runtime.resolve("scheduler.reconcile_interval_seconds")).value)),
        news_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.news_scan_interval_seconds")).value)),
        signal_broadcast_interval_seconds=max(0, int((await runtime.resolve("scheduler.signal_broadcast_interval_seconds")).value)),
        execution_loop_interval_seconds=max(0, int((await runtime.resolve("scheduler.execution_loop_interval_seconds")).value)),
        risk_cycle_interval_seconds=max(0, int((await runtime.resolve("scheduler.risk_cycle_interval_seconds")).value)),
        position_monitor_interval_seconds=max(0, int((await runtime.resolve("scheduler.position_monitor_interval_seconds")).value)),
        scout_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.scout_scan_interval_seconds")).value)),
        tradecopy_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.tradecopy_scan_interval_seconds")).value)),
        options_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.options_scan_interval_seconds")).value)),
        swingtrade_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.swingtrade_scan_interval_seconds")).value)),
        miner_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.miner_scan_interval_seconds")).value)),
        evo_scan_interval_seconds=max(0, int((await runtime.resolve("scheduler.evo_scan_interval_seconds")).value)),
        runtime_proof_interval_seconds=max(0, int((await runtime.resolve("scheduler.runtime_proof_interval_seconds")).value)),
        autopilot_enabled=bool((await runtime.resolve_bool("scheduler.autopilot_enabled")).value),
        autopilot_interval_seconds=max(0, int((await runtime.resolve("scheduler.autopilot_interval_seconds")).value)),
    )


def _add_job(*, jobs: list[ScheduledJob], job: ScheduledJob) -> None:
    jobs.append(job)
    logger.info(
        "scheduler job add name={} interval_seconds={} run_on_start={}",
        job.name,
        job.interval_seconds,
        job.run_on_start,
    )


def _coerce_json_value(value: object) -> JSONValue:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    if isinstance(value, dict):
        return _coerce_json_dict(value)
    return str(value)


def _coerce_json_dict(payload: dict[str, object]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in payload.items():
        if isinstance(key, str):
            result[key] = _coerce_json_value(value)
    return result


def _nested_int(payload: dict[str, object], section: str, key: str) -> int:
    section_value = payload.get(section)
    if not isinstance(section_value, dict):
        return 0
    raw = section_value.get(key)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _nested_list(payload: dict[str, object], section: str, key: str) -> list[str]:
    section_value = payload.get(section)
    if not isinstance(section_value, dict):
        return []
    raw = section_value.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]
