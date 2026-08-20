from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.services.control_plane.types import SchedulerJobSnapshot, SchedulerSnapshot
from loguru import logger

from infra.scheduler.jobs import ScheduledJob, register_jobs

if TYPE_CHECKING:
    from runtime.container import Container


class SchedulerRunner:
    def __init__(self, jobs: list[ScheduledJob]) -> None:
        self._jobs = jobs
        self._stop_event = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._run_count: dict[str, int] = {job.name: 0 for job in jobs}
        self._last_run_at: dict[str, str | None] = {job.name: None for job in jobs}
        self._last_error: dict[str, str | None] = {job.name: None for job in jobs}
        self._active = False

    async def start(self) -> None:
        if self._active:
            logger.warning("scheduler start ignored because runner is already active")
            return
        self._active = True
        self._stop_event.clear()
        logger.info("scheduler start job_count={}", len(self._jobs))
        for job in self._jobs:
            task_name = f"scheduler:{job.name}"
            self._tasks[job.name] = asyncio.create_task(self._run_job_loop(job), name=task_name)
            logger.info(
                "scheduler task add job={} interval_seconds={} run_on_start={}",
                job.name,
                job.interval_seconds,
                job.run_on_start,
            )

    async def stop(self) -> None:
        if not self._active:
            logger.warning("scheduler stop ignored because runner is not active")
            return
        self._active = False
        self._stop_event.set()
        logger.info("scheduler stop start active_tasks={}", len(self._tasks))
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("scheduler stop completed")

    def snapshot(self) -> SchedulerSnapshot:
        return {
            "enabled": bool(self._jobs),
            "active": self._active,
            "job_count": len(self._jobs),
            "jobs": [
                SchedulerJobSnapshot(
                    name=job.name,
                    interval_seconds=job.interval_seconds,
                    run_on_start=job.run_on_start,
                    run_count=self._run_count.get(job.name, 0),
                    last_run_at=self._last_run_at.get(job.name),
                    last_error=self._last_error.get(job.name),
                    active=job.name in self._tasks and not self._tasks[job.name].done(),
                )
                for job in self._jobs
            ],
        }

    async def _run_job_loop(self, job: ScheduledJob) -> None:
        job_logger = logger.bind(scheduler_job=job.name)
        job_logger.debug(
            "scheduler job loop start job={} run_on_start={} interval_seconds={}",
            job.name,
            job.run_on_start,
            job.interval_seconds,
        )
        if not job.run_on_start:
            await self._sleep_until_next_tick(job.interval_seconds)
        while not self._stop_event.is_set():
            await self._run_job(job)
            await self._sleep_until_next_tick(job.interval_seconds)
        job_logger.debug("scheduler job loop stop job={}", job.name)

    async def _run_job(self, job: ScheduledJob) -> None:
        job_logger = logger.bind(scheduler_job=job.name)
        started_at = datetime.now(timezone.utc)
        self._last_run_at[job.name] = started_at.isoformat()
        job_logger.info("scheduler job run start name={} started_at={}", job.name, self._last_run_at[job.name])
        try:
            with logger.contextualize(scheduler_job=job.name):
                await job.run()
            self._run_count[job.name] = self._run_count.get(job.name, 0) + 1
            self._last_error[job.name] = None
            finished_at = datetime.now(timezone.utc)
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            job_logger.info(
                "scheduler job finish name={} run_count={} duration_ms={}",
                job.name,
                self._run_count[job.name],
                duration_ms,
            )
        except Exception as exc:
            self._last_error[job.name] = str(exc)[:500]
            job_logger.exception("scheduler job error name={} error={}", job.name, self._last_error[job.name])

    async def _sleep_until_next_tick(self, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=float(interval_seconds))
        except TimeoutError:
            return


async def start_scheduler(container: Container) -> SchedulerRunner | None:
    jobs = await register_jobs(container)
    if not jobs:
        logger.info("scheduler disabled because no jobs were registered")
        return None
    runner = SchedulerRunner(jobs=jobs)
    await runner.start()
    logger.info("scheduler runner started")
    return runner
