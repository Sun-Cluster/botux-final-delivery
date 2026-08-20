from __future__ import annotations

from loguru import logger
from datetime import datetime
from typing import cast

from pydantic import JsonValue

from runtime.logging import format_log_fields
from db.models import SignalEvent, SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase
from domain.enums import SignalStatus
from domain.models.signal import Signal


pipeline_logger = logger.bind(pipeline_module=__name__)

class SignalsRepository(RepositoryBase):
    async def save_signal(self, signal: Signal) -> None:
        record = await self._query(SignalRecord.filter(signal_id=signal.signal_id)).first()
        if record is None:
            record = SignalRecord(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                action=signal.action.value,
                status=signal.status.value,
                score=signal.score,
                confidence=signal.confidence,
                priority=signal.priority,
                source=signal.source,
                headline=signal.headline,
                lane_hint=signal.lane_hint,
                strategy_hint=signal.strategy_hint,
                dedup_key=signal.dedup_key,
                scan_timestamp=signal.scan_timestamp,
                blocked_reason=signal.blocked_reason,
                metadata=signal.metadata,
                schema_version=signal.schema_version,
                created_at=signal.created_at,
            )
            await self._save(record)
            event_type = "SignalCreated"
        else:
            record.symbol = signal.symbol
            record.action = signal.action.value
            record.status = signal.status.value
            record.score = signal.score
            record.confidence = signal.confidence
            record.priority = signal.priority
            record.source = signal.source
            record.headline = signal.headline
            record.lane_hint = signal.lane_hint
            record.strategy_hint = signal.strategy_hint
            record.dedup_key = signal.dedup_key
            record.scan_timestamp = signal.scan_timestamp
            record.blocked_reason = signal.blocked_reason  # type: ignore[bad-assignment]
            record.metadata = signal.metadata
            record.schema_version = signal.schema_version
            await self._save(record)
            event_type = "SignalUpdated"

        payload = _signal_payload(signal)
        event = SignalEvent(
            signal=record,
            event_type=event_type,
            payload=payload,
        )
        await self._save(event)
        await append_outbox_event(
            event_type=event_type,
            entity_key=signal.signal_id,
            payload=payload,
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.stored", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "action": signal.action.value, "status": signal.status.value, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint, "event_type": event_type}))

    async def list_pending(self, limit: int = 100) -> list[Signal]:
        rows = await self._query(SignalRecord.filter(status=SignalStatus.PENDING.value)).order_by("created_at").limit(limit)
        return [
            Signal(
                signal_id=row.signal_id,
                symbol=row.symbol,
                action=row.action,
                score=row.score or 0.0,
                confidence=row.confidence,
                priority=row.priority,
                source=row.source or "unknown",
                headline=row.headline,
                lane_hint=row.lane_hint,
                strategy_hint=row.strategy_hint,
                dedup_key=row.dedup_key,
                scan_timestamp=row.scan_timestamp,
                blocked_reason=row.blocked_reason,
                metadata=_metadata_dict(row.metadata),
                status=row.status,
                schema_version=row.schema_version,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def list_recent(self, limit: int = 100) -> list[Signal]:
        rows = await self._query(SignalRecord.all()).order_by("-created_at").limit(limit)
        return [
            Signal(
                signal_id=row.signal_id,
                symbol=row.symbol,
                action=row.action,
                score=row.score or 0.0,
                confidence=row.confidence,
                priority=row.priority,
                source=row.source or "unknown",
                headline=row.headline,
                lane_hint=row.lane_hint,
                strategy_hint=row.strategy_hint,
                dedup_key=row.dedup_key,
                scan_timestamp=row.scan_timestamp,
                blocked_reason=row.blocked_reason,
                metadata=_metadata_dict(row.metadata),
                status=row.status,
                schema_version=row.schema_version,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def get_by_signal_id(self, signal_id: str) -> Signal | None:
        row = await self._query(SignalRecord.filter(signal_id=signal_id)).first()
        if row is None:
            return None
        return Signal(
            signal_id=row.signal_id,
            symbol=row.symbol,
            action=row.action,
            score=row.score or 0.0,
            confidence=row.confidence,
            priority=row.priority,
            source=row.source or "unknown",
            headline=row.headline,
            lane_hint=row.lane_hint,
            strategy_hint=row.strategy_hint,
            dedup_key=row.dedup_key,
            scan_timestamp=row.scan_timestamp,
            blocked_reason=row.blocked_reason,
            metadata=_metadata_dict(row.metadata),
            status=row.status,
            schema_version=row.schema_version,
            created_at=row.created_at,
        )

    async def set_status(self, signal_id: str, status: SignalStatus, *, reason: str | None = None) -> bool:
        row = await self._query(SignalRecord.filter(signal_id=signal_id)).first()
        if row is None:
            return False

        row.status = status.value
        metadata = _metadata_dict(row.metadata)
        if reason is not None:
            metadata["last_status_reason"] = reason
            metadata["last_status_reason_status"] = status.value
        if status == SignalStatus.APPROVED:
            if reason is not None:
                metadata["approval_reason"] = reason
            metadata.pop("failure_reason", None)
        elif status in {SignalStatus.REJECTED, SignalStatus.FAILED}:
            if reason is not None:
                metadata["failure_reason"] = reason
        elif status == SignalStatus.EXECUTED:
            metadata.pop("failure_reason", None)
        row.metadata = metadata
        if reason is not None:
            if status in {SignalStatus.REJECTED, SignalStatus.FAILED}:
                row.blocked_reason = reason
            else:
                row.blocked_reason = None  # type: ignore[bad-assignment]
        elif status not in {SignalStatus.REJECTED, SignalStatus.FAILED}:
            row.blocked_reason = None  # type: ignore[bad-assignment]
        await self._save(row)
        payload: dict[str, JSONValue] = {
            "signal_id": signal_id,
            "status": status.value,
            "reason": reason,
        }
        signal_event = SignalEvent(
            signal=row,
            event_type="SignalStatusUpdated",
            payload=payload,
        )
        await self._save(signal_event)
        await append_outbox_event(
            event_type="SignalStatusUpdated",
            entity_key=signal_id,
            payload=payload,
            connection=self._connection,
        )
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.status_updated", format_log_fields({"signal_id": signal_id, "status": status.value, "reason": reason}))
        return True

    async def count_pending(self) -> int:
        return int(await self._query(SignalRecord.filter(status=SignalStatus.PENDING.value)).count())

    async def count_pending_older_than(self, cutoff: datetime) -> int:
        return int(
            await self._query(
                SignalRecord.filter(
                    status=SignalStatus.PENDING.value,
                    created_at__lt=cutoff,
                )
            ).count()
        )

    async def list_pending_older_than(self, cutoff: datetime, *, limit: int = 100) -> list[Signal]:
        rows = (
            await self._query(
                SignalRecord.filter(
                    status=SignalStatus.PENDING.value,
                    created_at__lt=cutoff,
                )
            )
            .order_by("created_at")
            .limit(limit)
        )
        return [
            Signal(
                signal_id=row.signal_id,
                symbol=row.symbol,
                action=row.action,
                score=row.score or 0.0,
                confidence=row.confidence,
                priority=row.priority,
                source=row.source or "unknown",
                headline=row.headline,
                lane_hint=row.lane_hint,
                strategy_hint=row.strategy_hint,
                dedup_key=row.dedup_key,
                scan_timestamp=row.scan_timestamp,
                blocked_reason=row.blocked_reason,
                metadata=_metadata_dict(row.metadata),
                status=row.status,
                schema_version=row.schema_version,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def auto_retry_failed_signals(self, *, max_attempts: int = 3, limit: int = 100) -> int:
        rows = (
            await self._query(SignalRecord.filter(status=SignalStatus.FAILED.value))
            .order_by("created_at")
            .limit(limit)
        )
        retried_count = 0
        for row in rows:
            metadata = _metadata_dict(row.metadata)
            retry_count = int(metadata.get("retry_count", 0))
            if retry_count < max_attempts:
                metadata["retry_count"] = retry_count + 1
                metadata["last_status_reason"] = f"Auto-retry attempt {retry_count + 1}"
                metadata["last_status_reason_status"] = SignalStatus.PENDING.value

                row.status = SignalStatus.PENDING.value
                row.blocked_reason = None
                row.metadata = metadata
                await self._save(row)

                payload = {
                    "signal_id": row.signal_id,
                    "status": SignalStatus.PENDING.value,
                    "reason": f"auto_retry_attempt_{retry_count + 1}",
                }
                event = SignalEvent(
                    signal=row,
                    event_type="SignalStatusUpdated",
                    payload=payload,
                )
                await self._save(event)
                await append_outbox_event(
                    event_type="SignalStatusUpdated",
                    entity_key=row.signal_id,
                    payload=payload,
                    connection=self._connection,
                )
                retried_count += 1
                pipeline_logger.log(
                    "INFO",
                    "pipeline.{} {}",
                    "signal.auto_retried",
                    format_log_fields({"signal_id": row.signal_id, "symbol": row.symbol, "attempt": retry_count + 1}),
                )
        return retried_count


def _metadata_dict(value: object) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return {str(key): cast(JsonValue, item) for key, item in value.items()}
    return {}


def _signal_payload(signal: Signal) -> dict[str, JSONValue]:
    return {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "action": signal.action.value,
        "status": signal.status.value,
        "score": signal.score,
        "confidence": signal.confidence,
        "priority": signal.priority,
        "source": signal.source,
        "headline": signal.headline,
        "lane_hint": signal.lane_hint,
        "strategy_hint": signal.strategy_hint,
        "dedup_key": signal.dedup_key,
        "scan_timestamp": signal.scan_timestamp.isoformat() if signal.scan_timestamp is not None else None,
        "blocked_reason": signal.blocked_reason,
        "metadata": signal.metadata,
        "schema_version": signal.schema_version,
        "created_at": signal.created_at.isoformat(),
    }
