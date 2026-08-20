from __future__ import annotations

from domain.models.gate_decision import GateFailureDetail
from domain.models.signal import Signal


def evaluate_admission(signal: Signal) -> list[GateFailureDetail]:
    failures: list[GateFailureDetail] = []
    if signal.blocked_reason:
        failures.append(
            GateFailureDetail(
                gate_name="admission.blocked_reason",
                reason=signal.blocked_reason,
                payload={"blocked_reason": signal.blocked_reason},
            )
        )
    metadata = signal.metadata
    if metadata.get("duplicate_within_window") is True:
        failures.append(
            GateFailureDetail(
                gate_name="admission.dedup",
                reason="duplicate_within_window",
                payload={"dedup_key": signal.dedup_key},
            )
        )
    return failures
