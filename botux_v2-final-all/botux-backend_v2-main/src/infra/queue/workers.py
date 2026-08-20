from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

from app.services.control_plane.types import WorkerRuntimeSnapshot
from infra.queue.bus import InProcessQueueBus
from infra.queue.envelope import QueueEnvelope
from infra.queue.retry import next_retry_envelope, should_retry

QueueHandler = Callable[[QueueEnvelope], Awaitable[None]]

class WorkerRuntime:
    def __init__(self, bus: InProcessQueueBus, *, max_attempts: int = 3) -> None:
        self.bus = bus
        self.max_attempts = max_attempts
        self._handlers: dict[str, QueueHandler] = {}
        self._processed_ids: set[str] = set()
        self._active = False
        self._worker_task: asyncio.Task[None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._processed_count = 0
        self._retry_count = 0
        self._dead_letter_count = 0
        self._error_count = 0
        self._dedup_skip_count = 0
        self._unknown_type_count = 0

    def register_handler(self, msg_type: str, handler: QueueHandler) -> None:
        self._handlers[msg_type] = handler

    async def run_retry_once(self) -> None:
        try:
            envelope = await asyncio.wait_for(self.bus.retry_queue.get(), timeout=0.1)
        except TimeoutError:
            return
        try:
            now = datetime.now(timezone.utc)
            if envelope.available_at <= now:
                await self.bus.publish(envelope)
            else:
                await self.bus.publish_retry(envelope)
                await asyncio.sleep(0.05)
        finally:
            self.bus.retry_queue.task_done()

    async def run_once(self) -> None:
        try:
            message = await asyncio.wait_for(self.bus.work_queue.get(), timeout=0.1)
        except TimeoutError:
            return
        try:
            if message.msg_id in self._processed_ids:
                self._dedup_skip_count += 1
                return
            handler = self._handlers.get(message.msg_type)
            if handler is None:
                self._unknown_type_count += 1
                self._dead_letter_count += 1
                await self.bus.publish_dead_letter(
                    message.model_copy(update={"last_error": f"unknown_msg_type:{message.msg_type}"})
                )
                return

            await handler(message)
            self._processed_ids.add(message.msg_id)
            self._processed_count += 1
        except Exception as exc:
            self._error_count += 1
            if should_retry(message, max_attempts=self.max_attempts):
                self._retry_count += 1
                retry_message = next_retry_envelope(message, error_message=str(exc))
                await self.bus.publish_retry(retry_message)
            else:
                self._dead_letter_count += 1
                dead_letter = message.model_copy(update={"last_error": str(exc)[:500]})
                await self.bus.publish_dead_letter(dead_letter)
        finally:
            self.bus.work_queue.task_done()

    async def run(self) -> None:
        self._active = True
        while self._active:
            await self.run_once()

    async def run_retry_loop(self) -> None:
        self._active = True
        while self._active:
            await self.run_retry_once()

    async def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self.run())
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self.run_retry_loop())

    async def stop(self) -> None:
        self._active = False
        tasks = [task for task in (self._worker_task, self._retry_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def snapshot(self) -> WorkerRuntimeSnapshot:
        return {
            "active": self._active,
            "registered_handlers": sorted(self._handlers.keys()),
            "processed_count": self._processed_count,
            "retry_count": self._retry_count,
            "dead_letter_count": self._dead_letter_count,
            "error_count": self._error_count,
            "dedup_skip_count": self._dedup_skip_count,
            "unknown_type_count": self._unknown_type_count,
        }
