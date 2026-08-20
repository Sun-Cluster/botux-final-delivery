from __future__ import annotations

from pydantic import JsonValue

from domain.models.gate_decision import CouncilVote, GateFailureDetail
from domain.models.signal import Signal


def evaluate_risk_voter(signal: Signal) -> tuple[CouncilVote, list[GateFailureDetail]]:
    metadata = signal.metadata
    failures: list[GateFailureDetail] = []

    if _as_bool(metadata.get("trading_halted")):
        failures.append(
            GateFailureDetail(
                gate_name="risk.halt",
                reason=str(metadata.get("trading_halt_reason") or "trading_halted"),
                veto=True,
                payload={"trading_halted": True},
            )
        )
    consecutive_losses = _as_int(metadata.get("consecutive_losses"))
    if consecutive_losses >= 4:
        failures.append(
            GateFailureDetail(
                gate_name="risk.consecutive_losses",
                reason=f"consecutive_losses={consecutive_losses}",
                veto=True,
                payload={"consecutive_losses": consecutive_losses},
            )
        )
    if _as_bool(metadata.get("correlation_blocked")):
        failures.append(
            GateFailureDetail(
                gate_name="risk.correlation",
                reason=str(metadata.get("correlation_reason") or "correlation_blocked"),
                veto=True,
                payload=_json_dict(
                    {
                        "correlated_with": metadata.get("correlated_with"),
                        "sector_overlap": metadata.get("sector_overlap"),
                    }
                ),
            )
        )
    if metadata.get("pdt_allowed") is False:
        failures.append(
            GateFailureDetail(
                gate_name="risk.pdt",
                reason=str(metadata.get("pdt_reason") or "pdt_blocked"),
                payload={"pdt_allowed": False},
            )
        )

    vetoed = any(failure.veto for failure in failures)
    if vetoed:
        primary = next(failure for failure in failures if failure.veto)
        return (
            CouncilVote(
                voter="risk_engine",
                vote="skip",
                confidence=1.0,
                weight=1.0,
                reasoning=f"VETO: {primary.reason}",
                veto=True,
                evidence={"failures": [failure.model_dump(mode="json") for failure in failures]},
            ),
            failures,
        )

    if failures:
        return (
            CouncilVote(
                voter="risk_engine",
                vote="skip",
                confidence=0.75,
                weight=1.0,
                reasoning="Risk soft block",
                evidence={"failures": [failure.model_dump(mode="json") for failure in failures]},
            ),
            failures,
        )

    return (
        CouncilVote(
            voter="risk_engine",
            vote="buy",
            confidence=0.8,
            weight=1.0,
            reasoning="Risk within limits",
            evidence={
                "trading_halted": False,
                "consecutive_losses": consecutive_losses,
                "correlation_blocked": False,
            },
        ),
        [],
    )


def _as_bool(value: object) -> bool:
    return value is True


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _json_dict(payload: dict[str, object]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, list):
            normalized: list[JsonValue] = []
            for item in value:
                if isinstance(item, (str, int, float, bool)) or item is None:
                    normalized.append(item)
            result[key] = normalized
    return result
