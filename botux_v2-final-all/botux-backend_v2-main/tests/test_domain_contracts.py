from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.enums import CouncilDecision, ExecutionStatus, OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.execution_result import ExecutionResult
from domain.models.gate_decision import CouncilVote, GateDecision, GateFailureDetail
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


def test_signal_contract_valid_and_strict() -> None:
    model = Signal(
        signal_id="sig-1",
        symbol="aapl",
        action=OrderAction.BUY,
        score=0.8,
        source="SCOUT_ALPHA",
        lane_hint="SwingTrade",
        strategy_hint="Momentum_V1",
        headline="  Momentum setup  ",
        status=SignalStatus.PENDING,
    )
    assert model.status == SignalStatus.PENDING
    assert model.symbol == "AAPL"
    assert model.source == "scout_alpha"
    assert model.lane_hint == "swingtrade"
    assert model.strategy_hint == "momentum_v1"
    assert model.headline == "Momentum setup"
    assert model.confidence == 0.8
    assert model.scan_timestamp == model.created_at
    assert model.dedup_key == "momentum_v1:AAPL:buy"

    with pytest.raises(ValidationError):
        Signal(
            signal_id="sig-1",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=1.2,
            status=SignalStatus.PENDING,
        )

    with pytest.raises(ValidationError):
        Signal(
            signal_id="sig-1",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            status=SignalStatus.PENDING,
            unknown_field="x",
        )

    with pytest.raises(ValidationError):
        Signal(
            signal_id="sig-1",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            priority=11,
            status=SignalStatus.PENDING,
        )


def test_gate_decision_contract() -> None:
    model = GateDecision(
        signal_id="sig-2",
        decision=CouncilDecision.APPROVE,
        reason="ok",
        confidence=0.72,
        buy_votes=3.0,
        total_votes=5,
        votes=[
            CouncilVote(
                voter="technical",
                vote="buy",
                confidence=0.8,
                reasoning="strong setup",
            )
        ],
        failures=[
            GateFailureDetail(
                gate_name="risk.correlation",
                reason="blocked",
                veto=True,
            )
        ],
    )
    assert model.decision == CouncilDecision.APPROVE
    assert model.votes[0].vote == "buy"
    assert model.failures[0].gate_name == "risk.correlation"

    with pytest.raises(ValidationError):
        GateDecision(
            signal_id="sig-2",
            decision="APPROVE",  # uppercase should fail because enum value is lowercase
            reason="ok",
        )

    with pytest.raises(ValidationError):
        CouncilVote(
            voter="technical",
            vote="hold",
            confidence=0.8,
            reasoning="bad enum",
        )


def test_order_intent_contract() -> None:
    model = OrderIntent(
        signal_id="sig-3",
        symbol="msft",
        action=OrderAction.SELL,
        quantity=2.5,
        idempotency_key=" idem-3 ",
        broker_name=" ALPACA ",
        market=" US_EQUITIES ",
        order_type=" LIMIT ",
        lane_hint="SwingTrade",
        strategy_hint="Momentum_V1",
    )
    assert model.action == OrderAction.SELL
    assert model.symbol == "MSFT"
    assert model.idempotency_key == "idem-3"
    assert model.broker_name == "alpaca"
    assert model.market == "us_equities"
    assert model.order_type == "limit"
    assert model.lane_hint == "swingtrade"
    assert model.strategy_hint == "momentum_v1"

    with pytest.raises(ValidationError):
        OrderIntent(
            signal_id="sig-3",
            symbol="MSFT",
            action=OrderAction.SELL,
            quantity=0,
            idempotency_key="idem-3",
        )


def test_execution_result_contract() -> None:
    model = ExecutionResult(
        order_id="100",
        status=ExecutionStatus.SUBMITTED,
        filled_qty=0,
        avg_price=None,
    )
    assert model.status == ExecutionStatus.SUBMITTED

    with pytest.raises(ValidationError):
        ExecutionResult(
            order_id="100",
            status="submittedd",
            filled_qty=0,
            avg_price=None,
        )

    with pytest.raises(ValidationError):
        ExecutionResult(
            order_id="100",
            status=ExecutionStatus.SUBMITTED,
            filled_qty=-1,
            avg_price=None,
        )


def test_trade_outcome_contract() -> None:
    model = TradeOutcome(
        trade_id="t-1",
        signal_id="sig-4",
        symbol="NVDA",
        outcome=TradeOutcomeStatus.WIN,
        pnl_pct=1.4,
    )
    assert model.outcome == TradeOutcomeStatus.WIN

    with pytest.raises(ValidationError):
        TradeOutcome(
            trade_id="t-1",
            signal_id="sig-4",
            symbol="NVDA",
            outcome="WIN",
            pnl_pct=1.4,
        )
