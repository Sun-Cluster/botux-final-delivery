from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tortoise import Tortoise

from db.models import TradeOutcomeRecord
from db.repositories.orders_repo import OrdersRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import ExecutionStatus, OrderAction, TradeOutcomeStatus
from domain.models.execution_result import ExecutionResult
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal


async def _setup_db() -> None:
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


async def _teardown_db() -> None:
    await Tortoise.close_connections()


async def _run_record_execution_entry_close_semantics_case() -> None:
    await _setup_db()
    signals_repo = SignalsRepository(connection=None)
    orders_repo = OrdersRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)

    buy_signal = Signal(
        signal_id="sig-buy-001",
        symbol="AAPL",
        action=OrderAction.BUY,
        score=0.92,
        source="tradecopy",
        lane_hint="tradecopy",
        strategy_hint="institutional_replication",
    )
    await signals_repo.save_signal(buy_signal)
    buy_order_id = await orders_repo.create_order_intent(
        OrderIntent(
            signal_id=buy_signal.signal_id,
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=2.0,
            idempotency_key="order-buy-001",
            metadata={"signal_source": "tradecopy", "bot_id": "copycat"},
        )
    )

    buy_opened = await outcomes_repo.record_execution_entry(
        ExecutionResult(
            order_id=buy_order_id,
            status=ExecutionStatus.FILLED,
            broker_order_id="brk-buy-001",
            filled_qty=2.0,
            avg_price=100.0,
            created_at=datetime.now(timezone.utc),
        )
    )
    assert buy_opened is not None
    assert buy_opened.outcome == TradeOutcomeStatus.OPEN.value
    assert buy_opened.closed_at is None

    sell_signal = Signal(
        signal_id="sig-sell-001",
        symbol="AAPL",
        action=OrderAction.SELL,
        score=1.0,
        source="tradecopy",
        lane_hint="tradecopy",
        strategy_hint="position_exit",
        metadata={"exit_reason": "profit_target"},
    )
    await signals_repo.save_signal(sell_signal)
    sell_order_id = await orders_repo.create_order_intent(
        OrderIntent(
            signal_id=sell_signal.signal_id,
            symbol="AAPL",
            action=OrderAction.SELL,
            quantity=2.0,
            idempotency_key="order-sell-001",
            metadata={"signal_source": "tradecopy", "bot_id": "copycat"},
        )
    )

    sell_closed = await outcomes_repo.record_execution_entry(
        ExecutionResult(
            order_id=sell_order_id,
            status=ExecutionStatus.FILLED,
            broker_order_id="brk-sell-001",
            filled_qty=2.0,
            avg_price=110.0,
            created_at=datetime.now(timezone.utc),
        )
    )
    assert sell_closed is not None
    assert sell_closed.outcome in {TradeOutcomeStatus.WIN.value, TradeOutcomeStatus.LOSS.value}
    assert sell_closed.outcome != TradeOutcomeStatus.OPEN.value
    assert sell_closed.close_reason == "take_profit"
    assert sell_closed.closed_at is not None

    total_rows = await TradeOutcomeRecord.filter(symbol="AAPL").count()
    open_rows = await TradeOutcomeRecord.filter(symbol="AAPL", outcome=TradeOutcomeStatus.OPEN.value).count()
    assert total_rows == 1
    assert open_rows == 0

    await _teardown_db()


def test_record_execution_entry_buy_opens_and_sell_closes_existing_open_outcome() -> None:
    asyncio.run(_run_record_execution_entry_close_semantics_case())
