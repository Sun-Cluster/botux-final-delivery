from __future__ import annotations

from domain.models.gate_decision import GateDecision
from domain.models.signal import Signal
from domain.rules.admission_rules import evaluate_admission
from domain.rules.council_rules import deliberate_signal
from domain.rules.risk_rules import evaluate_risk_voter


class GateService:
    async def evaluate(self, signal: Signal) -> GateDecision:
        admission_failures = evaluate_admission(signal)
        risk_vote, risk_failures = evaluate_risk_voter(signal)
        return deliberate_signal(
            signal,
            risk_vote=risk_vote,
            risk_failures=risk_failures,
            admission_failures=admission_failures,
        )
