from __future__ import annotations

import asyncio

from tortoise import Tortoise

from app.services.gate.service import GateService
from db.models import CouncilDecisionRecord, GateFailure
from db.repositories.council_repo import CouncilRepository
from db.repositories.signals_repo import SignalsRepository
from domain.enums import CouncilDecision, OrderAction, SignalStatus
from domain.models.signal import Signal


def test_council_majority_and_veto_behaviors() -> None:
    asyncio.run(_run_council_majority_and_veto_behaviors())


def test_council_repository_persists_lineage_and_failures() -> None:
    asyncio.run(_run_council_repository_persists_lineage_and_failures())


async def _run_council_majority_and_veto_behaviors() -> None:
    service = GateService()

    approved = await service.evaluate(
        Signal(
            signal_id="sig-council-approve",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.82,
            confidence=0.84,
            source="scout",
            metadata={"sentiment": 0.42, "ml_ready": True, "ml_score": 0.76, "ml_stage": 2, "regime": "bull"},
            status=SignalStatus.PENDING,
        )
    )
    assert approved.decision == CouncilDecision.APPROVE
    assert approved.buy_votes >= 3.0
    assert approved.confidence >= 0.55
    assert approved.position_size_pct is not None
    assert len(approved.votes) == 5

    risk_veto = await service.evaluate(
        Signal(
            signal_id="sig-council-risk-veto",
            symbol="NVDA",
            action=OrderAction.BUY,
            score=0.88,
            confidence=0.9,
            source="scout",
            metadata={"sentiment": 0.5, "trading_halted": True, "trading_halt_reason": "daily_loss_limit"},
            status=SignalStatus.PENDING,
        )
    )
    assert risk_veto.decision == CouncilDecision.VETO
    assert risk_veto.vetoed is True
    assert risk_veto.veto_reason == "daily_loss_limit"
    assert any(failure.gate_name == "risk.halt" for failure in risk_veto.failures)

    crisis_veto = await service.evaluate(
        Signal(
            signal_id="sig-council-crisis",
            symbol="SPY",
            action=OrderAction.BUY,
            score=0.77,
            confidence=0.8,
            source="scout",
            metadata={"sentiment": 0.15, "regime": "crisis", "ml_ready": True, "ml_score": 0.7},
            status=SignalStatus.PENDING,
        )
    )
    assert crisis_veto.decision == CouncilDecision.VETO
    assert crisis_veto.veto_reason == "CRISIS regime"

    asx_relaxed = await service.evaluate(
        Signal(
            signal_id="sig-council-asx",
            symbol="BHP.AX",
            action=OrderAction.BUY,
            score=0.62,
            confidence=0.62,
            source="ausmine",
            lane_hint="miner",
            metadata={"regime": "bear", "ml_ready": False, "sentiment": 0.0},
            status=SignalStatus.PENDING,
        )
    )
    assert asx_relaxed.decision == CouncilDecision.APPROVE
    assert asx_relaxed.buy_votes == 2.0


async def _run_council_repository_persists_lineage_and_failures() -> None:
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {"models": {"models": ["src.db.models"], "default_connection": "default"}},
            "use_tz": True,
            "timezone": "UTC",
        },
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas(safe=True)

    signals_repo = SignalsRepository(connection=None)
    council_repo = CouncilRepository(connection=None)
    service = GateService()

    signal = Signal(
        signal_id="sig-council-db",
        symbol="AMD",
        action=OrderAction.BUY,
        score=0.79,
        confidence=0.8,
        source="scout",
        metadata={"sentiment": 0.25, "correlation_blocked": True, "correlation_reason": "too_correlated", "correlated_with": ["NVDA", "AAPL"]},
        status=SignalStatus.PENDING,
    )
    await signals_repo.save_signal(signal)
    decision = await service.evaluate(signal)
    await council_repo.save_decision(decision)

    rows = await CouncilDecisionRecord.all().order_by("-created_at")
    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "veto"
    assert row.vetoed is True
    assert row.votes_count == 5
    assert row.failures_count >= 1
    failures = await GateFailure.filter(signal_id="sig-council-db").all()
    assert len(failures) == 1
    assert failures[0].gate_name == "risk.correlation"
    assert failures[0].correlation_blocked is True

    await Tortoise.close_connections()
