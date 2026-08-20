from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Iterator

from tortoise import Tortoise

from app.services.execution.service import ExecutionService
from app.services.order_status.reconcile import OrderStatusReconcileService
from app.services.registry.bootstrap import bootstrap_canonical_registry
from app.services.signals.service import SignalService
from app.usecases.process_pending_signals import process_pending_signals
from app.usecases.submit_order import submit_order
from db.models import CouncilDecisionRecord, ExecutionRecord, OrderRecord, OutboxEvent, TradeOutcomeRecord
from db.repositories.signals_repo import SignalsRepository
from domain.enums import OrderAction, SignalStatus
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from infra.brokers.base import BrokerPort
from infra.brokers.router import BrokerRouter


@contextmanager
def _patched_env(**updates: str) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update(updates)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FakeFilledBroker(BrokerPort):
    async def get_account(self) -> dict:
        return {"equity": 100000}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "price": 100}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        return {
            "status": "filled",
            "broker_order_id": "brk-test-001",
            "filled_qty": 1,
            "avg_price": 101.5,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled"}


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


async def _run_flow_case() -> None:
    await _setup_db()
    try:
        with _patched_env(BOTUX_BYPASS_BOT_LIFECYCLE="1", BOTUX_BYPASS_MARKET_HOURS="1"):
            await bootstrap_canonical_registry()
            repo = SignalsRepository(connection=None)
            await repo.save_signal(
                Signal(
                    signal_id="sig-flow-approve",
                    symbol="MSFT",
                    action=OrderAction.BUY,
                    score=0.9,
                    source="turbo",
                    lane_hint="turbo",
                    strategy_hint="intraday_momentum",
                    metadata={"execution_bot_id": "turbo", "bot_id": "turbo"},
                    status=SignalStatus.PENDING,
                )
            )
            await repo.save_signal(
                Signal(
                    signal_id="sig-flow-reject",
                    symbol="TSLA",
                    action=OrderAction.BUY,
                    score=0.3,
                    source="turbo",
                    lane_hint="turbo",
                    strategy_hint="intraday_momentum",
                    metadata={"execution_bot_id": "turbo", "bot_id": "turbo"},
                    status=SignalStatus.PENDING,
                )
            )

            execution_service = ExecutionService(broker=FakeFilledBroker())
            result = await process_pending_signals(limit=10, quantity=1.0, execution_service=execution_service)

            assert result == {"processed": 2, "executed": 1, "rejected": 1, "errors": 0, "enqueued": 0}
            approved_signal = await repo.get_by_signal_id("sig-flow-approve")
            rejected_signal = await repo.get_by_signal_id("sig-flow-reject")
            assert approved_signal is not None
            assert rejected_signal is not None
            assert approved_signal.status == SignalStatus.EXECUTED
            assert rejected_signal.status == SignalStatus.REJECTED
            assert await CouncilDecisionRecord.all().count() == 2
            assert await OrderRecord.all().count() == 1
            assert await ExecutionRecord.all().count() == 1
            outcome = await TradeOutcomeRecord.all().first()
            assert outcome is not None
            assert outcome.outcome == "open"
            assert outcome.pnl_pct == 0.0
            assert outcome.entry_price == 101.5
            assert outcome.bot_id == "turbo"
    finally:
        await _teardown_db()


def test_process_pending_signals_flow() -> None:
    asyncio.run(_run_flow_case())


async def _run_execution_service_without_broker_case() -> None:
    order = OrderIntent(
        signal_id="sig-missing-broker",
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=1.0,
        idempotency_key="sig-missing-broker:order",
        broker_name="alpaca",
        market="us_equities",
        order_type="market",
    )
    result = await ExecutionService(broker=None).submit("missing-broker-order", order)
    assert result.status.value == "failed"
    assert result.broker_order_id is None
    assert result.filled_qty == 0.0


def test_execution_service_fails_when_broker_is_unavailable() -> None:
    asyncio.run(_run_execution_service_without_broker_case())


class FakeDelayedFillBroker(BrokerPort):
    def __init__(self) -> None:
        self.status_calls = 0

    async def get_account(self) -> dict:
        return {"equity": 100000}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        return {
            "status": "submitted",
            "broker_order_id": "brk-delay-001",
            "filled_qty": 0.0,
            "avg_price": None,
            "symbol": order_intent.symbol,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        self.status_calls += 1
        if self.status_calls <= 3:
            return {"broker_order_id": broker_order_id, "status": "submitted", "filled_qty": 0.0, "avg_price": None}
        return {"broker_order_id": broker_order_id, "status": "filled", "filled_qty": 1.0, "avg_price": 102.25}


class FakeHistoryBackfillBroker(BrokerPort):
    async def get_account(self) -> dict:
        return {"equity": 100000}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        return {
            "status": "submitted",
            "broker_order_id": "unused",
            "filled_qty": 0.0,
            "avg_price": None,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "submitted", "filled_qty": 0.0, "avg_price": None}

    async def list_orders(self, *, status: str = "all", limit: int = 100) -> list[dict]:
        _ = status
        _ = limit
        return [
            {
                "id": "alpaca-hist-1",
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "qty": 2.0,
                "filled_qty": 2.0,
                "filled_avg_price": 101.25,
                "type": "market",
                "submitted_at": "2026-05-31T01:01:01Z",
                "updated_at": "2026-05-31T01:02:00Z",
            }
        ]


class FakeCanonicalBackfillBroker(BrokerPort):
    async def get_account(self) -> dict:
        return {"equity": 100000}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        return {
            "status": "submitted",
            "broker_order_id": "alpaca-canon-1",
            "filled_qty": 0.0,
            "avg_price": None,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "submitted", "filled_qty": 0.0, "avg_price": None}

    async def list_orders(self, *, status: str = "all", limit: int = 100) -> list[dict]:
        _ = status
        _ = limit
        return [
            {
                "id": "alpaca-canon-1",
                "symbol": "AAPL",
                "side": "buy",
                "status": "filled",
                "qty": 1.0,
                "filled_qty": 1.0,
                "filled_avg_price": 100.5,
                "type": "market",
                "submitted_at": "2026-05-31T01:01:01Z",
                "updated_at": "2026-05-31T01:02:00Z",
            }
        ]


async def _run_order_status_reconcile_case() -> None:
    await _setup_db()
    try:
        with _patched_env(BOTUX_BYPASS_BOT_LIFECYCLE="1", BOTUX_BYPASS_MARKET_HOURS="1"):
            await bootstrap_canonical_registry()
            broker = FakeDelayedFillBroker()
            router = BrokerRouter(default_broker=broker, brokers={"alpaca": broker})
            signal = Signal(
                signal_id="sig-order-reconcile",
                symbol="AAPL",
                action=OrderAction.BUY,
                score=0.9,
                source="manual",
                lane_hint="manual",
                strategy_hint="manual_order",
                metadata={"execution_bot_id": "turbo", "bot_id": "turbo"},
                status=SignalStatus.PENDING,
            )
            await SignalService().ingest_signal(signal)
            result = await submit_order(
                signal,
                quantity=1.0,
                execution_service=ExecutionService(broker=broker),
                broker_router=router,
            )
            assert result is not None
            assert result.status.value == "submitted"
            order = await OrderRecord.get(id=int(result.order_id))
            assert order.status == "submitted"
            assert await TradeOutcomeRecord.all().count() == 0

            reconcile = OrderStatusReconcileService(router)
            stats = await reconcile.reconcile_active_orders(limit=10)
            assert stats["updated"] == 1
            assert stats["filled"] == 1

            order = await OrderRecord.get(id=int(result.order_id))
            assert order.status == "executed"
            outcome = await TradeOutcomeRecord.get(order_id=int(result.order_id))
            assert outcome.outcome == "open"
            assert outcome.entry_price == 102.25
    finally:
        await _teardown_db()


def test_order_status_reconcile_updates_submitted_order_to_filled() -> None:
    asyncio.run(_run_order_status_reconcile_case())


async def _run_order_status_reconcile_backfills_broker_history_case() -> None:
    await _setup_db()
    try:
        broker = FakeHistoryBackfillBroker()
        router = BrokerRouter(default_broker=broker, brokers={"alpaca": broker})
        reconcile = OrderStatusReconcileService(router)

        first = await reconcile.reconcile_active_orders(limit=10)
        assert first["backfill_checked"] == 1
        assert first["backfill_imported"] == 1
        assert first["backfill_executions"] == 1
        assert first["backfill_filled"] == 1
        assert await OrderRecord.all().count() == 1
        assert await ExecutionRecord.all().count() == 1
        assert await TradeOutcomeRecord.all().count() == 1
        imported_order = await OrderRecord.all().first()
        assert imported_order is not None
        assert imported_order.bot_id == "unknown"
        imported_outcome = await TradeOutcomeRecord.all().first()
        assert imported_outcome is not None
        assert imported_outcome.bot_id == "unknown"

        second = await reconcile.reconcile_active_orders(limit=10)
        assert second["backfill_checked"] == 1
        assert second["backfill_imported"] == 0
        assert second["backfill_executions"] == 0
        assert await OrderRecord.all().count() == 1
        assert await ExecutionRecord.all().count() == 1
    finally:
        await _teardown_db()


def test_order_status_reconcile_backfills_broker_history_without_duplicates() -> None:
    asyncio.run(_run_order_status_reconcile_backfills_broker_history_case())


async def _run_order_status_reconcile_backfill_reuses_existing_order_case() -> None:
    await _setup_db()
    try:
        with _patched_env(BOTUX_BYPASS_BOT_LIFECYCLE="1", BOTUX_BYPASS_MARKET_HOURS="1"):
            await bootstrap_canonical_registry()
            broker = FakeCanonicalBackfillBroker()
            router = BrokerRouter(default_broker=broker, brokers={"alpaca": broker})
            signal = Signal(
                signal_id="sig-canonical-backfill",
                symbol="AAPL",
                action=OrderAction.BUY,
                score=0.92,
                source="alpaca_news",
                lane_hint="news",
                strategy_hint="newsfeed_intel",
                metadata={"execution_bot_id": "turbo", "bot_id": "turbo"},
                status=SignalStatus.PENDING,
            )
            await SignalService().ingest_signal(signal)
            submit_result = await submit_order(
                signal,
                quantity=1.0,
                execution_service=ExecutionService(broker=broker),
                broker_router=router,
            )
            assert submit_result is not None
            original_order_id = int(submit_result.order_id)

            reconcile = OrderStatusReconcileService(router)
            stats = await reconcile.reconcile_active_orders(limit=10)

            assert stats["backfill_checked"] == 1
            assert stats["backfill_imported"] == 0
            assert stats["backfill_executions"] == 1
            assert stats["backfill_filled"] == 1
            assert await OrderRecord.all().count() == 1
            outcome = await TradeOutcomeRecord.get(order_id=original_order_id)
            assert outcome.bot_id == "turbo"
            assert outcome.entry_price == 100.5
    finally:
        await _teardown_db()


def test_order_status_reconcile_backfill_reuses_existing_order() -> None:
    asyncio.run(_run_order_status_reconcile_backfill_reuses_existing_order_case())

# From test_execution_routing.py
class RoutingFilledBroker(BrokerPort):
    def __init__(self, *, broker_name: str) -> None:
        self.broker_name = broker_name
        self.submissions: list[OrderIntent] = []

    async def get_account(self) -> dict:
        return {"equity": 100000, "cash": 50000, "broker": self.broker_name}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        self.submissions.append(order_intent)
        return {
            "status": "filled",
            "broker_order_id": f"{self.broker_name}-filled-1",
            "filled_qty": order_intent.quantity,
            "avg_price": 100.25,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled"}


async def _run_execution_routing_case() -> None:
    await _setup_db()
    try:
        with _patched_env(BOTUX_BYPASS_BOT_LIFECYCLE="1", BOTUX_BYPASS_MARKET_HOURS="1"):
            await bootstrap_canonical_registry()

            alpaca = RoutingFilledBroker(broker_name="alpaca")
            ibkr = RoutingFilledBroker(broker_name="ibkr")
            router = BrokerRouter(default_broker=alpaca, brokers={"alpaca": alpaca, "ibkr": ibkr})

            turbo_signal = Signal(
                signal_id="sig-route-turbo",
                symbol="NVDA",
                action=OrderAction.BUY,
                score=0.93,
                source="turbo",
                lane_hint="turbo",
                strategy_hint="intraday_momentum",
                status=SignalStatus.PENDING,
                metadata={
                    "bot_id": "turbo",
                    "execution_bot_id": "turbo",
                    "regime": "bull",
                    "sentiment": 0.8,
                    "ml_score": 0.91,
                    "reference_price": 100.25,
                },
            )
            await SignalService().ingest_signal(turbo_signal)
            turbo_execution = await submit_order(turbo_signal, quantity=2.0, broker_router=router)
            assert turbo_execution is not None

            turbo_order = await OrderRecord.get(id=int(turbo_execution.order_id))
            assert turbo_order.broker_name == "alpaca"
            assert turbo_order.market == "us_equities"
            assert turbo_order.order_type == "bracket"
            assert turbo_order.bot_id == "turbo"
            assert turbo_order.route_reason == "profile:alpaca"
            assert alpaca.submissions[0].order_type == "bracket"

            turbo_outcome = await TradeOutcomeRecord.get(order_id=int(turbo_execution.order_id))
            assert turbo_outcome.broker_name == "alpaca"
            assert turbo_outcome.order_type == "bracket"
            assert turbo_outcome.bot_id == "turbo"

            nugget_signal = Signal(
                signal_id="sig-route-nugget",
                symbol="BHP.AX",
                action=OrderAction.BUY,
                score=0.88,
                source="ausmining",
                lane_hint="miner",
                strategy_hint="event_driven",
                status=SignalStatus.PENDING,
                metadata={
                    "bot_id": "nugget_bot",
                    "execution_bot_id": "nugget_bot",
                    "regime": "bull",
                    "sentiment": 0.7,
                    "ml_score": 0.82,
                    "reference_price": 100.1,
                },
            )
            await SignalService().ingest_signal(nugget_signal)
            nugget_execution = await submit_order(nugget_signal, quantity=1.0, broker_router=router)
            assert nugget_execution is not None

            nugget_order = await OrderRecord.get(id=int(nugget_execution.order_id))
            assert nugget_order.broker_name == "ibkr"
            assert nugget_order.market == "asx_equities"
            assert nugget_order.order_type == "limit"
            assert nugget_order.bot_id == "nugget_bot"
            assert ibkr.submissions[0].order_type == "limit"

            order_requested_events = await OutboxEvent.filter(event_type="OrderRequested").order_by("id")
            assert len(order_requested_events) == 2
            assert order_requested_events[1].payload["broker_name"] == "ibkr"
            assert order_requested_events[1].payload["market"] == "asx_equities"
            assert order_requested_events[1].payload["order_type"] == "limit"
    finally:
        await _teardown_db()


def test_submit_order_routes_and_persists_execution_intent() -> None:
    asyncio.run(_run_execution_routing_case())
