from __future__ import annotations

from typing import cast

from db.models import CouncilDecisionRecord, GateFailure, SignalRecord
from db.repositories._common import JSONValue, append_outbox_event
from db.repositories.base import RepositoryBase
from domain.models.gate_decision import GateDecision


class CouncilRepository(RepositoryBase):
    async def save_decision(self, decision: GateDecision) -> None:
        signal = await self._query(SignalRecord.filter(signal_id=decision.signal_id)).first()
        if signal is None:
            raise ValueError(f"signal not found: {decision.signal_id}")

        payload = {
            "signal_id": decision.signal_id,
            "decision": decision.decision.value,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "buy_votes": decision.buy_votes,
            "total_votes": decision.total_votes,
            "vetoed": decision.vetoed,
            "veto_reason": decision.veto_reason,
            "approval_score": decision.approval_score,
            "position_size_pct": decision.position_size_pct,
            "stop_loss_pct": decision.stop_loss_pct,
            "take_profit_pct": decision.take_profit_pct,
            "votes": [vote.model_dump(mode="json") for vote in decision.votes],
            "failures": [failure.model_dump(mode="json") for failure in decision.failures],
            "evidence": decision.evidence,
            "schema_version": decision.schema_version,
            "created_at": decision.created_at.isoformat(),
        }
        record = CouncilDecisionRecord(
            signal=signal,
            decision=decision.decision.value,
            reason=decision.reason,
            confidence=decision.confidence,
            buy_votes=decision.buy_votes,
            total_votes=decision.total_votes,
            vetoed=decision.vetoed,
            veto_reason=decision.veto_reason,
            approval_score=decision.approval_score,
            position_size_pct=decision.position_size_pct,
            stop_loss_pct=decision.stop_loss_pct,
            take_profit_pct=decision.take_profit_pct,
            votes_count=len(decision.votes),
            failures_count=len(decision.failures),
            schema_version=decision.schema_version,
            created_at=decision.created_at,
        )
        await self._save(record)

        if decision.failures:
            for failure in decision.failures:
                failure_payload = failure.payload if isinstance(failure.payload, dict) else {}
                gate_failure = GateFailure(
                    signal_id=decision.signal_id,
                    gate_name=failure.gate_name,
                    reason=failure.reason,
                    decision=decision.decision.value,
                    veto=failure.veto,
                    confidence=decision.confidence,
                    buy_votes=decision.buy_votes,
                    blocked_reason=_optional_text(failure_payload.get("blocked_reason")),
                    dedup_key=_optional_text(failure_payload.get("dedup_key")),
                    trading_halted=_as_bool(failure_payload.get("trading_halted")),
                    trading_halt_reason=_optional_text(failure_payload.get("trading_halt_reason")),
                    consecutive_losses=_optional_int(failure_payload.get("consecutive_losses")),
                    correlation_blocked=(
                        _as_bool(failure_payload.get("correlation_blocked")) or failure.gate_name == "risk.correlation"
                    ),
                    correlation_reason=_optional_text(failure_payload.get("correlation_reason")),
                    correlated_with_csv=_csv(failure_payload.get("correlated_with")),
                    sector_overlap=_as_bool(failure_payload.get("sector_overlap")),
                    pdt_allowed=_optional_bool(failure_payload.get("pdt_allowed")),
                )
                await self._save(gate_failure)
        elif decision.decision.value != "approve":
            gate_failure = GateFailure(
                signal_id=decision.signal_id,
                gate_name="COUNCIL",
                reason=decision.reason,
                decision=decision.decision.value,
                veto=decision.vetoed,
                confidence=decision.confidence,
                buy_votes=decision.buy_votes,
            )
            await self._save(gate_failure)

        await append_outbox_event(
            event_type="GateEvaluated",
            entity_key=decision.signal_id,
            payload=cast(dict[str, JSONValue], payload),
            connection=self._connection,
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool:
    return value is True


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return None


def _csv(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    items = [str(item).strip() for item in value if str(item).strip()]
    if not items:
        return None
    return ",".join(items)
