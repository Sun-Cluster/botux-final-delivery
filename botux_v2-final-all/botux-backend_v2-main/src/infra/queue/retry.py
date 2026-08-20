from __future__ import annotations

from datetime import datetime, timedelta, timezone

from infra.queue.envelope import QueueEnvelope


def bump_attempt(envelope: QueueEnvelope) -> QueueEnvelope:
    return envelope.model_copy(update={"attempt": envelope.attempt + 1})


def should_retry(envelope: QueueEnvelope, *, max_attempts: int) -> bool:
    return envelope.attempt + 1 < max_attempts


def compute_backoff_seconds(attempt: int, *, base_seconds: float = 0.25, max_seconds: float = 5.0) -> float:
    backoff = base_seconds * (2 ** max(attempt, 0))
    return min(backoff, max_seconds)


def next_retry_envelope(
    envelope: QueueEnvelope,
    *,
    error_message: str,
    base_seconds: float = 0.25,
    max_seconds: float = 5.0,
) -> QueueEnvelope:
    next_attempt = envelope.attempt + 1
    backoff_seconds = compute_backoff_seconds(next_attempt, base_seconds=base_seconds, max_seconds=max_seconds)
    available_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
    return envelope.model_copy(
        update={
            "attempt": next_attempt,
            "available_at": available_at,
            "last_error": error_message[:500],
        }
    )
