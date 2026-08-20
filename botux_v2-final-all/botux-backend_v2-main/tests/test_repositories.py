from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tortoise import Tortoise

from db.models import GateFailure, OrderRecord, OutboxEvent, SignalRecord, TradeOutcomeRecord
from db.repositories.bots_repo import BotsRepository
from db.repositories.council_repo import CouncilRepository
from db.repositories.executions_repo import ExecutionsRepository
from db.repositories.orders_repo import OrdersRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import CouncilDecision, ExecutionStatus, OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.execution_result import ExecutionResult
from domain.models.gate_decision import GateDecision
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


async def _run_repository_flow() -> None:
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
    orders_repo = OrdersRepository(connection=None)
    executions_repo = ExecutionsRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)
    bots_repo = BotsRepository(connection=None)

    signal = Signal(
        signal_id="sig-001",
        symbol="AAPL",
        action=OrderAction.BUY,
        score=0.81,
        confidence=0.74,
        priority=2,
        source="scout_engine",
        headline="AAPL breakout setup",
        lane_hint="swingtrade",
        strategy_hint="momentum_v2",
        metadata={"raw_score": 81.0, "scan_id": "scan-001"},
        status=SignalStatus.PENDING,
    )
    await signals_repo.save_signal(signal)
    await signals_repo.save_signal(signal.model_copy(update={"score": 0.83}))

    pending = await signals_repo.list_pending()
    assert len(pending) == 1
    assert pending[0].signal_id == "sig-001"
    assert pending[0].source == "scout_engine"
    assert pending[0].lane_hint == "swingtrade"
    assert pending[0].strategy_hint == "momentum_v2"
    assert pending[0].dedup_key == "momentum_v2:AAPL:buy"
    assert pending[0].metadata["scan_id"] == "scan-001"
    row = await SignalRecord.get(signal_id="sig-001")
    assert row.confidence == 0.74
    assert row.priority == 2
    assert row.headline == "AAPL breakout setup"
    assert row.source == "scout_engine"
    assert row.blocked_reason is None

    approved = GateDecision(
        signal_id="sig-001",
        decision=CouncilDecision.APPROVE,
        reason="score above threshold",
    )
    await council_repo.save_decision(approved)

    rejected = GateDecision(
        signal_id="sig-001",
        decision=CouncilDecision.REJECT,
        reason="risk veto",
    )
    await council_repo.save_decision(rejected)
    assert await GateFailure.all().count() == 1

    order_intent = OrderIntent(
        signal_id="sig-001",
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=5,
        idempotency_key="idem-001",
        broker_name="alpaca",
        market="us_equities",
        order_type="bracket",
        lane_hint="turbo",
        strategy_hint="intraday_momentum",
        metadata={"bot_id": "turbo", "reference_price": 187.25},
    )
    order_id = await orders_repo.create_order_intent(order_intent)
    assert order_id.isdigit()
    same_order_id = await orders_repo.create_order_intent(order_intent)
    assert same_order_id == order_id
    order_row = await OrderRecord.get(id=int(order_id))
    assert order_row.broker_name == "alpaca"
    assert order_row.market == "us_equities"
    assert order_row.order_type == "bracket"
    assert order_row.bot_id == "turbo"
    assert order_row.reference_price == 187.25

    execution = ExecutionResult(
        order_id=order_id,
        status=ExecutionStatus.FILLED,
        broker_order_id="brk-001",
        filled_qty=5,
        avg_price=187.25,
    )
    await executions_repo.save_execution(execution)

    updated_signal = await signals_repo.get_by_signal_id("sig-001")
    assert updated_signal is not None
    assert updated_signal.status == SignalStatus.EXECUTED
    assert updated_signal.blocked_reason is None

    changed = await signals_repo.set_status("sig-001", SignalStatus.APPROVED, reason="bypass_council")
    assert changed is True
    approved_signal = await signals_repo.get_by_signal_id("sig-001")
    assert approved_signal is not None
    assert approved_signal.blocked_reason is None
    assert approved_signal.metadata["approval_reason"] == "bypass_council"

    changed = await signals_repo.set_status("sig-001", SignalStatus.REJECTED, reason="duplicate_within_window")
    assert changed is True
    rejected_signal = await signals_repo.get_by_signal_id("sig-001")
    assert rejected_signal is not None
    assert rejected_signal.blocked_reason == "duplicate_within_window"
    assert rejected_signal.metadata["failure_reason"] == "duplicate_within_window"

    failed_execution = ExecutionResult(
        order_id=order_id,
        status=ExecutionStatus.REJECTED,
        error_reason="alpaca_not_configured",
        broker_order_id=None,
        filled_qty=0,
        avg_price=None,
    )
    await executions_repo.save_execution(failed_execution)
    failed_signal = await signals_repo.get_by_signal_id("sig-001")
    assert failed_signal is not None
    assert failed_signal.status == SignalStatus.FAILED
    assert failed_signal.blocked_reason == "alpaca_not_configured"
    assert failed_signal.metadata["failure_reason"] == "alpaca_not_configured"

    retried = await signals_repo.auto_retry_failed_signals(max_attempts=3)
    assert retried == 1
    retried_signal = await signals_repo.get_by_signal_id("sig-001")
    assert retried_signal is not None
    assert retried_signal.status == SignalStatus.PENDING
    assert retried_signal.blocked_reason is None
    assert retried_signal.metadata["retry_count"] == 1

    await executions_repo.save_execution(failed_execution)
    failed_signal_2 = await signals_repo.get_by_signal_id("sig-001")
    assert failed_signal_2 is not None
    assert failed_signal_2.status == SignalStatus.FAILED
    assert failed_signal_2.metadata["retry_count"] == 1

    retried_2 = await signals_repo.auto_retry_failed_signals(max_attempts=3)
    assert retried_2 == 1
    retried_signal_2 = await signals_repo.get_by_signal_id("sig-001")
    assert retried_signal_2 is not None
    assert retried_signal_2.status == SignalStatus.PENDING
    assert retried_signal_2.metadata["retry_count"] == 2

    await executions_repo.save_execution(failed_execution)

    retried_3 = await signals_repo.auto_retry_failed_signals(max_attempts=3)
    assert retried_3 == 1
    retried_signal_3 = await signals_repo.get_by_signal_id("sig-001")
    assert retried_signal_3 is not None
    assert retried_signal_3.status == SignalStatus.PENDING
    assert retried_signal_3.metadata["retry_count"] == 3

    await executions_repo.save_execution(failed_execution)

    retried_4 = await signals_repo.auto_retry_failed_signals(max_attempts=3)
    assert retried_4 == 0
    failed_signal_final = await signals_repo.get_by_signal_id("sig-001")
    assert failed_signal_final is not None
    assert failed_signal_final.status == SignalStatus.FAILED
    assert failed_signal_final.metadata["retry_count"] == 3

    outcome = TradeOutcome(
        trade_id=order_id,
        signal_id="sig-001",
        symbol="AAPL",
        outcome=TradeOutcomeStatus.WIN,
        pnl_pct=2.1,
        closed_at=datetime.now(timezone.utc),
    )
    await outcomes_repo.save_outcome(outcome)
    await TradeOutcomeRecord.create(
        signal=row,
        order=None,
        symbol="TSLA",
        outcome=TradeOutcomeStatus.OPEN.value,
        pnl_pct=0.0,
        features={
            "trade_id": "reference-trade-1",
            "action": "sell",
            "entry_price": 205.5,
            "quantity": 3.0,
            "bot_id": "reference_bot",
            "source": "reference_source",
        },
    )
    await TradeOutcomeRecord.create(
        signal=row,
        order=None,
        symbol="MSFT",
        outcome=TradeOutcomeStatus.WIN.value,
        pnl_pct=1.8,
        features={
            "trade_id": "reference-trade-2",
            "action": "buy",
            "entry_price": 310.0,
            "exit_price": 315.58,
            "qty": 4.0,
            "source": "tradecopy",
            "close_reason": "target_hit",
        },
    )
    recent = await outcomes_repo.list_recent(limit=10)
    assert len(recent) == 3
    assert recent[0].trade_id in {order_id, "2", "3"}
    prior_recent = next(item for item in recent if item.symbol == "TSLA")
    assert prior_recent.trade_id == "2"
    assert prior_recent.action is None
    assert prior_recent.entry_price is None
    assert prior_recent.quantity is None
    assert prior_recent.bot_id is None
    assert prior_recent.source is None
    prior_recent_second = next(item for item in recent if item.symbol == "MSFT")
    assert prior_recent_second.trade_id == "3"
    assert prior_recent_second.quantity is None
    assert prior_recent_second.bot_id is None
    assert prior_recent_second.close_reason is None

    await bots_repo.upsert_bot_profile("turbo", {"enabled": True, "market": "US"})
    profile = await bots_repo.get_bot_profile("turbo")
    assert profile is not None
    assert profile["enabled"] is True

    assert await OutboxEvent.all().count() >= 6

    await Tortoise.close_connections()


def test_repositories_end_to_end_sqlite() -> None:
    asyncio.run(_run_repository_flow())
