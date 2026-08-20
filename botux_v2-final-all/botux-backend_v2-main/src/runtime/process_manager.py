from __future__ import annotations

from loguru import logger
from uuid import uuid4

from app.services.control_plane.types import ProcessManagerSnapshot
from runtime.logging import format_log_fields
from app.usecases.submit_order import submit_order
from domain.models.signal import Signal
from infra.queue.envelope import QueueEnvelope
from infra.queue.workers import WorkerRuntime
from runtime.cpu_pool import CpuTaskRunner
from runtime.cpu_tasks import preprocess_signal_payload
from runtime.container import Container

pipeline_logger = logger.bind(pipeline_module=__name__)

class ProcessManager:
    def __init__(self, container: Container) -> None:
        self._container = container
        self._workers: list[WorkerRuntime] = []
        self._cpu_pool = CpuTaskRunner(max_workers=container.config.cpu_worker_concurrency)
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        worker_count = max(self._container.config.io_worker_concurrency, 1)
        self._workers = [
            WorkerRuntime(self._container.queue_bus, max_attempts=3) for _ in range(worker_count)
        ]
        for worker in self._workers:
            worker.register_handler("signal.process", self._handle_signal_process)
            await worker.start()

    async def stop(self) -> None:
        self._running = False
        for worker in self._workers:
            await worker.stop()
        self._workers.clear()
        await self._cpu_pool.shutdown()

    async def _handle_signal_process(self, envelope: QueueEnvelope) -> None:
        normalized_payload = await self._cpu_pool.run(preprocess_signal_payload, envelope.payload)
        signal = Signal.model_validate(normalized_payload)
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.processing_started", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "runner": "queue_worker", "trace_id": envelope.trace_id, "msg_id": envelope.msg_id}))
        try:
            result = await submit_order(signal, broker_router=self._container.broker_router)
        except Exception:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "signal.processing_failed", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "runner": "queue_worker", "trace_id": envelope.trace_id, "msg_id": envelope.msg_id}))
            raise
        if result is None:
            pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.processing_finished", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "result": "rejected", "runner": "queue_worker", "trace_id": envelope.trace_id, "msg_id": envelope.msg_id}))
            return
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.processing_finished", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "result": result.status.value, "runner": "queue_worker", "trace_id": envelope.trace_id, "msg_id": envelope.msg_id, "order_id": result.order_id}))

    async def publish_signal(self, signal: Signal) -> None:
        envelope = QueueEnvelope(
            msg_id=f"sig:{signal.signal_id}:{uuid4().hex}",
            msg_type="signal.process",
            payload=signal.model_dump(mode="json"),
            trace_id=signal.signal_id,
        )
        await self._container.queue_bus.publish(envelope)
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.enqueued", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "queue": envelope.msg_type, "msg_id": envelope.msg_id, "trace_id": envelope.trace_id}))

    def snapshot(self) -> ProcessManagerSnapshot:
        return {
            "running": self._running,
            "io_worker_target": max(self._container.config.io_worker_concurrency, 1),
            "active_workers": len(self._workers),
            "workers": [worker.snapshot() for worker in self._workers],
            "cpu_pool": self._cpu_pool.snapshot(),
        }
