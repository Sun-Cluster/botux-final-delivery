from __future__ import annotations

from domain.enums import CouncilDecision
from domain.models.gate_decision import CouncilVote, GateDecision, GateFailureDetail
from domain.models.signal import Signal


MIN_VOTES = 3.0
MIN_CONFIDENCE = 0.55
ASX_MIN_VOTES = 2.0
ASX_MIN_CONFIDENCE = 0.45
ML_STAGE_WEIGHTS = {
    0: 0.5,
    1: 1.0,
    2: 1.25,
    3: 1.5,
    4: 2.0,
}


def deliberate_signal(
    signal: Signal,
    *,
    risk_vote: CouncilVote,
    risk_failures: list[GateFailureDetail],
    admission_failures: list[GateFailureDetail],
) -> GateDecision:
    thresholds = _thresholds(signal)
    failures = [*admission_failures, *risk_failures]
    if admission_failures:
        return GateDecision(
            signal_id=signal.signal_id,
            decision=CouncilDecision.REJECT,
            reason=admission_failures[0].reason,
            failures=failures,
            evidence={"thresholds": thresholds, "phase": "admission"},
        )

    votes = [
        technical_vote(signal),
        news_vote(signal),
        risk_vote,
        ml_vote(signal),
        regime_vote(signal),
    ]
    total_votes = len(votes)
    buy_vote_weight = sum(vote.weight for vote in votes if vote.vote == "buy")
    weighted_buy = sum(vote.weight * vote.confidence for vote in votes if vote.vote == "buy")
    weighted_total = sum(vote.weight * vote.confidence for vote in votes)
    approval_score = weighted_buy / weighted_total if weighted_total > 0 else 0.0
    buy_confidences = [vote.confidence for vote in votes if vote.vote == "buy"]
    avg_confidence = sum(buy_confidences) / len(buy_confidences) if buy_confidences else 0.0

    veto_vote = next((vote for vote in votes if vote.veto), None)
    if veto_vote is not None:
        reason = veto_vote.reasoning.replace("VETO: ", "", 1)
        return GateDecision(
            signal_id=signal.signal_id,
            decision=CouncilDecision.VETO,
            reason=reason,
            confidence=0.0,
            buy_votes=buy_vote_weight,
            total_votes=total_votes,
            vetoed=True,
            veto_reason=reason,
            votes=votes,
            failures=failures or [GateFailureDetail(gate_name=f"{veto_vote.voter}.veto", reason=reason, veto=True)],
            approval_score=approval_score,
            evidence={
                "thresholds": thresholds,
                "weighted_buy": round(weighted_buy, 6),
                "weighted_total": round(weighted_total, 6),
            },
        )

    if buy_vote_weight >= thresholds["min_votes"] and avg_confidence >= thresholds["min_confidence"]:
        sizing = position_sizing(avg_confidence)
        stops = stop_policy(signal)
        return GateDecision(
            signal_id=signal.signal_id,
            decision=CouncilDecision.APPROVE,
            reason="council_approved",
            confidence=avg_confidence,
            buy_votes=buy_vote_weight,
            total_votes=total_votes,
            vetoed=False,
            votes=votes,
            failures=failures,
            approval_score=approval_score,
            position_size_pct=sizing,
            stop_loss_pct=stops["stop_loss_pct"],
            take_profit_pct=stops["take_profit_pct"],
            evidence={
                "thresholds": thresholds,
                "weighted_buy": round(weighted_buy, 6),
                "weighted_total": round(weighted_total, 6),
            },
        )

    reason = f"insufficient_support buy_weight={buy_vote_weight:.2f} avg_confidence={avg_confidence:.2f}"
    return GateDecision(
        signal_id=signal.signal_id,
        decision=CouncilDecision.REJECT,
        reason=reason,
        confidence=avg_confidence,
        buy_votes=buy_vote_weight,
        total_votes=total_votes,
        votes=votes,
        failures=failures,
        approval_score=approval_score,
        evidence={
            "thresholds": thresholds,
            "weighted_buy": round(weighted_buy, 6),
            "weighted_total": round(weighted_total, 6),
        },
    )


def technical_vote(signal: Signal) -> CouncilVote:
    if signal.score >= 0.75:
        confidence = min(0.95, max(signal.confidence or signal.score, 0.85))
        return CouncilVote(
            voter="technical",
            vote="buy",
            confidence=confidence,
            weight=1.0,
            reasoning=f"Strong technical {signal.score:.2f}",
            evidence={"score": signal.score},
        )
    if signal.score >= 0.60:
        confidence = min(0.70, max(signal.confidence or signal.score, 0.55))
        return CouncilVote(
            voter="technical",
            vote="buy",
            confidence=confidence,
            weight=1.0,
            reasoning=f"Moderate technical {signal.score:.2f}",
            evidence={"score": signal.score},
        )
    return CouncilVote(
        voter="technical",
        vote="skip",
        confidence=0.30,
        weight=1.0,
        reasoning=f"Weak technical {signal.score:.2f}",
        evidence={"score": signal.score},
    )


def news_vote(signal: Signal) -> CouncilVote:
    sentiment = _as_float(signal.metadata.get("sentiment"))
    confidence = signal.confidence or signal.score
    if sentiment > 0 and confidence > 0.30:
        return CouncilVote(
            voter="news_sentiment",
            vote="buy",
            confidence=min(max(confidence, 0.35), 0.9),
            weight=1.0,
            reasoning=f"Positive sentiment {sentiment:.2f}",
            evidence={"sentiment": sentiment, "headline": signal.headline},
        )
    if sentiment < 0 and confidence > 0.30:
        return CouncilVote(
            voter="news_sentiment",
            vote="sell",
            confidence=min(max(confidence, 0.35), 0.9),
            weight=1.0,
            reasoning=f"Negative sentiment {sentiment:.2f}",
            evidence={"sentiment": sentiment, "headline": signal.headline},
        )
    return CouncilVote(
        voter="news_sentiment",
        vote="skip",
        confidence=0.30,
        weight=1.0,
        reasoning="Neutral or low-confidence sentiment",
        evidence={"sentiment": sentiment, "headline": signal.headline},
    )


def ml_vote(signal: Signal) -> CouncilVote:
    raw_ml_score = signal.metadata.get("ml_score")
    ml_ready = signal.metadata.get("ml_ready")
    ml_stage = _as_int(signal.metadata.get("ml_stage"))
    weight = ML_STAGE_WEIGHTS.get(ml_stage, 1.0)
    ml_score = _normalized_score(raw_ml_score, default=signal.score)
    if ml_ready is False:
        if signal.score >= 0.65:
            return CouncilVote(
                voter="ml",
                vote="buy",
                confidence=0.50,
                weight=weight,
                reasoning="ML untrained, fallback to score",
                evidence={"ml_ready": False, "fallback_score": signal.score, "ml_stage": ml_stage},
            )
        return CouncilVote(
            voter="ml",
            vote="skip",
            confidence=0.40,
            weight=weight,
            reasoning="ML not ready",
            evidence={"ml_ready": False, "ml_stage": ml_stage},
        )
    if ml_score >= 0.60:
        return CouncilVote(
            voter="ml",
            vote="buy",
            confidence=min(max(ml_score, 0.60), 0.95),
            weight=weight,
            reasoning=f"ML positive {ml_score:.2f}",
            evidence={"ml_score": ml_score, "ml_stage": ml_stage},
        )
    return CouncilVote(
        voter="ml",
        vote="skip",
        confidence=0.40,
        weight=weight,
        reasoning=f"ML low confidence {ml_score:.2f}",
        evidence={"ml_score": ml_score, "ml_stage": ml_stage},
    )


def regime_vote(signal: Signal) -> CouncilVote:
    regime = str(signal.metadata.get("regime") or "neutral").strip().lower()
    if regime == "bull":
        return CouncilVote(
            voter="regime",
            vote="buy",
            confidence=0.90,
            weight=1.0,
            reasoning="BULL regime",
            evidence={"regime": regime},
        )
    if regime == "neutral":
        return CouncilVote(
            voter="regime",
            vote="buy",
            confidence=0.55,
            weight=1.0,
            reasoning="NEUTRAL regime",
            evidence={"regime": regime},
        )
    if regime == "crisis":
        return CouncilVote(
            voter="regime",
            vote="skip",
            confidence=1.0,
            weight=1.0,
            reasoning="VETO: CRISIS regime",
            veto=True,
            evidence={"regime": regime},
        )
    return CouncilVote(
        voter="regime",
        vote="skip",
        confidence=0.70,
        weight=1.0,
        reasoning=f"{regime.upper()} regime",
        evidence={"regime": regime},
    )


def position_sizing(avg_confidence: float) -> float:
    if avg_confidence >= 0.80:
        return 2.5
    if avg_confidence >= 0.65:
        return 1.5
    if avg_confidence >= 0.55:
        return 1.0
    return 0.0


def stop_policy(signal: Signal) -> dict[str, float]:
    text = f"{signal.source} {signal.lane_hint or ''} {signal.strategy_hint or ''}".lower()
    if signal.symbol.endswith(".AX") or "ausmine" in text or "nugget" in text or "miner" in text:
        return {"stop_loss_pct": 0.05, "take_profit_pct": 0.12}
    return {"stop_loss_pct": 0.03, "take_profit_pct": 0.09}


def _thresholds(signal: Signal) -> dict[str, float]:
    text = f"{signal.source} {signal.lane_hint or ''} {signal.strategy_hint or ''}".lower()
    is_asx = signal.symbol.endswith(".AX") or "ausmine" in text or "asx" in text or "nugget" in text
    return {
        "min_votes": ASX_MIN_VOTES if is_asx else MIN_VOTES,
        "min_confidence": ASX_MIN_CONFIDENCE if is_asx else MIN_CONFIDENCE,
        "is_asx_relaxed": is_asx,
    }


def _normalized_score(value: object, *, default: float) -> float:
    raw = _as_float(value)
    if raw > 1.0:
        raw = raw / 100.0
    if raw <= 0.0:
        raw = default
    return max(0.0, min(raw, 1.0))


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
