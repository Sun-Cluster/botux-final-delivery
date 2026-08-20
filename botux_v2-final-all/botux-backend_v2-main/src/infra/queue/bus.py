from __future__ import annotations

from asyncio import Queue

from infra.queue.envelope import QueueEnvelope


class InProcessQueueBus:
    def __init__(self) -> None:
        self.work_queue: Queue[QueueEnvelope] = Queue()
        self.retry_queue: Queue[QueueEnvelope] = Queue()
        self.dead_letter_queue: Queue[QueueEnvelope] = Queue()

    async def publish(self, envelope: QueueEnvelope) -> None:
        await self.work_queue.put(envelope)

    async def publish_retry(self, envelope: QueueEnvelope) -> None:
        await self.retry_queue.put(envelope)

    async def publish_dead_letter(self, envelope: QueueEnvelope) -> None:
        await self.dead_letter_queue.put(envelope)

    def snapshot_sizes(self) -> dict[str, int]:
        return {
            "work_queue": self.work_queue.qsize(),
            "retry_queue": self.retry_queue.qsize(),
            "dead_letter_queue": self.dead_letter_queue.qsize(),
        }
