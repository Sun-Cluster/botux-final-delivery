from __future__ import annotations

import asyncio
import os
from config import load_configs
from datetime import datetime, timezone
from infra.brokers.alpaca_adapter import AlpacaAdapter
from infra.brokers.ibkr_adapter import IbkrAdapter
from infra.queue.bus import InProcessQueueBus
from infra.queue.envelope import QueueEnvelope
from infra.queue.workers import WorkerRuntime
from infra.scheduler.jobs import ScheduledJob, register_jobs
from infra.scheduler.runner import SchedulerRunner, start_scheduler
from loguru import logger
from runtime.container import build_container
from runtime.cpu_pool import CpuTaskRunner
from runtime.cpu_tasks import busy_sum, worker_pid
from runtime.logging import format_log_fields


# From test_config_injection.py


def test_container_injects_alpaca_by_default() -> None:
    _set_base_env()
    os.environ["BOTUX_BROKER_MODE"] = "paper"
    container = build_container()
    assert isinstance(container.broker, AlpacaAdapter)


def test_container_injects_ibkr_by_mode() -> None:
    _set_base_env()
    os.environ["BOTUX_BROKER_MODE"] = "ibkr"
    container = build_container()
    assert isinstance(container.broker, IbkrAdapter)


def test_deprecated_skip_db_init_alias_maps_to_registry_bootstrap_flag() -> None:
    _set_base_env()
    os.environ.pop("BOTUX_SKIP_REGISTRY_BOOTSTRAP", None)
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    config = load_configs()
    assert config.skip_registry_bootstrap is True


def _set_base_env() -> None:
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    os.environ["BOTUX_SKIP_REGISTRY_BOOTSTRAP"] = "1"


# From test_cpu_runtime.py


def test_cpu_task_runner_uses_process_pool() -> None:
    asyncio.run(_run_process_pool_case())


async def _run_process_pool_case() -> None:
    runner = CpuTaskRunner(max_workers=1)
    try:
        pid = await runner.run(worker_pid)
        assert pid != os.getpid()

        cpu_task = asyncio.create_task(runner.run(busy_sum, 1_200_000))
        # Event loop should stay responsive while CPU work runs in process pool.
        await asyncio.wait_for(asyncio.sleep(0.05), timeout=0.2)
        result = await cpu_task
        assert result > 0
    finally:
        await runner.shutdown()


# From test_queue_runtime.py


def test_queue_worker_retry_dead_letter_and_idempotency() -> None:
    asyncio.run(_run_queue_runtime_case())


async def _run_queue_runtime_case() -> None:
    bus = InProcessQueueBus()
    runtime = WorkerRuntime(bus, max_attempts=2)
    state = {"success_count": 0, "fail_count": 0}

    async def success_handler(_envelope: QueueEnvelope) -> None:
        state["success_count"] += 1

    async def always_fail_handler(_envelope: QueueEnvelope) -> None:
        state["fail_count"] += 1
        raise RuntimeError("boom")

    runtime.register_handler("ok", success_handler)
    runtime.register_handler("fail", always_fail_handler)

    ok_envelope = QueueEnvelope(
        msg_id="ok-1",
        msg_type="ok",
        payload={"signal_id": "sig-01"},
        trace_id="sig-01",
    )
    await bus.publish(ok_envelope)
    await runtime.run_once()
    assert state["success_count"] == 1

    # Same msg_id should be ignored (idempotency guard).
    await bus.publish(ok_envelope)
    await runtime.run_once()
    assert state["success_count"] == 1

    fail_envelope = QueueEnvelope(
        msg_id="fail-1",
        msg_type="fail",
        payload={"signal_id": "sig-02"},
        trace_id="sig-02",
    )
    await bus.publish(fail_envelope)
    await runtime.run_once()
    assert state["fail_count"] == 1
    assert bus.retry_queue.qsize() == 1

    first_retry = await bus.retry_queue.get()
    bus.retry_queue.task_done()
    due_retry = first_retry.model_copy(update={"available_at": datetime.now(timezone.utc)})
    await bus.publish_retry(due_retry)
    await runtime.run_retry_once()
    await runtime.run_once()
    assert state["fail_count"] == 2
    assert bus.dead_letter_queue.qsize() == 1


# From test_scheduler_runtime.py


async def _run_scheduler_runner_case() -> None:
    run_log: list[str] = []

    async def sample_job() -> None:
        run_log.append("tick")

    runner = SchedulerRunner(
        jobs=[
            ScheduledJob(
                name="sample.job",
                interval_seconds=1,
                run=sample_job,
                run_on_start=True,
            )
        ]
    )
    await runner.start()
    await asyncio.sleep(0.05)
    snapshot = runner.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["active"] is True
    assert snapshot["job_count"] == 1
    assert len(run_log) >= 1
    await runner.stop()
    stopped = runner.snapshot()
    assert stopped["active"] is False


def test_scheduler_runner_executes_periodic_job() -> None:
    asyncio.run(_run_scheduler_runner_case())


async def _run_start_scheduler_no_jobs_case() -> None:
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS"] = "5"
    os.environ["BOTUX_RECONCILE_INTERVAL_SECONDS"] = "5"
    container = build_container()
    runner = await start_scheduler(container)
    assert runner is not None
    snapshot = runner.snapshot()
    assert snapshot["job_count"] > 0
    await runner.stop()


def test_start_scheduler_runs_with_skip_db_init() -> None:
    asyncio.run(_run_start_scheduler_no_jobs_case())


async def _run_register_scheduler_jobs_case() -> None:
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS"] = "15"
    os.environ["BOTUX_RECONCILE_INTERVAL_SECONDS"] = "120"
    os.environ["BOTUX_NEWS_SCAN_INTERVAL_SECONDS"] = "180"
    os.environ["BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS"] = "60"
    os.environ["BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS"] = "30"
    os.environ["BOTUX_RISK_CYCLE_INTERVAL_SECONDS"] = "120"
    os.environ["BOTUX_POSITION_MONITOR_INTERVAL_SECONDS"] = "60"
    os.environ["BOTUX_SCOUT_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_TRADECOPY_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_OPTIONS_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_SWINGTRADE_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_MINER_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_EVO_SCAN_INTERVAL_SECONDS"] = "300"
    os.environ["BOTUX_RUNTIME_PROOF_INTERVAL_SECONDS"] = "900"
    container = build_container()
    jobs = await register_jobs(container)
    names = {job.name for job in jobs}
    run_on_start = {job.name: job.run_on_start for job in jobs}
    assert "portfolio.snapshot" in names
    assert "runtime.outbox_dispatch" in names
    assert "reconcile.run" in names
    assert "scanner.news" in names
    assert "execution.signal_broadcast" in names
    assert "execution.loop" in names
    assert "risk.cycle" in names
    assert "positions.monitor" in names
    assert "exits.evo_catalyst" in names
    assert "scanner.scout" in names
    assert "scanner.tradecopy" in names
    assert "scanner.options" in names
    assert "scanner.swingtrade" in names
    assert "scanner.ausmine" in names
    assert "scanner.evo_catalyst" in names
    assert "runtime.proof_pack" in names
    assert run_on_start["scanner.news"] is True
    assert run_on_start["scanner.scout"] is True
    assert run_on_start["scanner.tradecopy"] is True
    assert run_on_start["scanner.options"] is True
    assert run_on_start["scanner.swingtrade"] is True
    assert run_on_start["scanner.ausmine"] is True
    assert run_on_start["scanner.evo_catalyst"] is True


def test_register_jobs_includes_scheduler_flows() -> None:
    asyncio.run(_run_register_scheduler_jobs_case())


# From test_pipeline_logging.py


def test_pipeline_logging_formats_context_fields() -> None:
    captured: list[str] = []
    sink_id = logger.add(captured.append, format="{message}")
    try:
        logger.log(
            "INFO",
            "pipeline.{} {}",
            "signal.created",
            format_log_fields(
                {
                    "signal_id": "sig-123",
                    "symbol": "AAPL",
                    "source": "alpaca_news",
                    "lane": "news",
                    "score": 0.82,
                    "metadata": {"headline": "AAPL news"},
                }
            ),
        )
    finally:
        logger.remove(sink_id)

    assert captured
    message = captured[-1]
    assert "pipeline.signal.created" in message
    assert 'signal_id="sig-123"' in message
    assert 'symbol="AAPL"' in message
    assert 'source="alpaca_news"' in message
    assert 'lane="news"' in message
    assert 'score=0.82' in message
    assert 'metadata={"headline":"AAPL news"}' in message
