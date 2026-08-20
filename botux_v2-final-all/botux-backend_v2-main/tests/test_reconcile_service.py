from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from tortoise import Tortoise

from app.services.reconcile.service import ReconcileService
from db.repositories.executions_repo import ExecutionsRepository
from db.repositories.orders_repo import OrdersRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import ExecutionStatus, OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.execution_result import ExecutionResult
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome
from infra.brokers.base import BrokerPort


class FakeReconBroker(BrokerPort):
    async def get_account(self) -> dict:
        return {
            "equity": 110000,
            "last_equity": 100000,
            "cash": 55000,
            "buying_power": 120000,
            "portfolio_value": 110000,
            "broker": "fake",
        }

    async def get_positions(self) -> list[dict]:
        return [
            {
                "symbol": "AAPL",
                "qty": 2,
                "market_value": 420,
                "avg_entry": 200,
                "current_price": 210,
                "unrealized_pl": 20,
                "unrealized_plpc": 0.05,
            },
            {
                "symbol": "TSLA",
                "qty": 1,
                "market_value": 190,
                "avg_entry": 220,
                "current_price": 190,
                "unrealized_pl": -30,
                "unrealized_plpc": -0.1363,
            },
        ]

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        return {"status": "submitted", "broker_order_id": f"fake-{order_intent.signal_id}"}

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "submitted"}


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


async def _run_reconcile_case() -> None:
    await _setup_db()
    now = datetime.now(timezone.utc)
    signals_repo = SignalsRepository(connection=None)
    orders_repo = OrdersRepository(connection=None)
    executions_repo = ExecutionsRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)

    await signals_repo.save_signal(
        Signal(
            signal_id="sig-stale",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            status=SignalStatus.PENDING,
            created_at=now - timedelta(days=2),
        )
    )
    await signals_repo.save_signal(
        Signal(
            signal_id="sig-pending-fresh",
            symbol="MSFT",
            action=OrderAction.BUY,
            score=0.7,
            status=SignalStatus.PENDING,
            created_at=now - timedelta(hours=2),
        )
    )
    await signals_repo.save_signal(
        Signal(
            signal_id="sig-exec-aapl",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.9,
            status=SignalStatus.PENDING,
        )
    )
    order_id = await orders_repo.create_order_intent(
        OrderIntent(
            signal_id="sig-exec-aapl",
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=2.0,
            idempotency_key="idem-recon-aapl",
        )
    )
    await executions_repo.save_execution(
        ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.FILLED,
            broker_order_id="brk-recon-1",
            filled_qty=2.0,
            avg_price=210.0,
            created_at=now,
        )
    )
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id=order_id,
            signal_id="sig-exec-aapl",
            symbol="AAPL",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            closed_at=now,
        )
    )

    await signals_repo.save_signal(
        Signal(
            signal_id="sig-open-msft",
            symbol="MSFT",
            action=OrderAction.BUY,
            score=0.65,
            status=SignalStatus.PENDING,
        )
    )
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="manual-open-msft",
            signal_id="sig-open-msft",
            symbol="MSFT",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            closed_at=now,
        )
    )

    service = ReconcileService(broker=FakeReconBroker())
    report = await service.run()

    assert report["source"] == "broker"
    assert report["signals_pending"] == 3
    assert report["signals_stale"] == 1
    assert report["trades_today"] == 1
    assert report["daily_pnl"] == 10000.0
    assert report["open_symbols_db"] == ["AAPL", "MSFT"]
    assert report["open_symbols_broker"] == ["AAPL", "TSLA"]
    assert report["db_truth"]["orders_today"] == 1
    assert report["db_truth"]["executions_today"] == 1
    assert report["db_truth"]["filled_executions_today"] == 1
    assert report["db_truth"]["open_outcomes"] == 2
    assert report["execution_summary"]["filled_without_open_outcome"] == []
    assert report["reconciliation"]["missing_in_broker"] == ["MSFT"]
    assert report["reconciliation"]["missing_in_db"] == ["TSLA"]
    assert len(report["stale_signals_details"]) == 1
    assert report["stale_signals_details"][0]["signal_id"] == "sig-stale"
    assert len(report["open_outcomes"]) == 2
    assert {row["symbol"] for row in report["open_outcomes"]} == {"AAPL", "MSFT"}
    assert len(report["positions"]) == 2
    assert len(report["winners"]) == 1
    assert len(report["losers"]) == 1
    issues = report["issues"]
    assert isinstance(issues, list)
    assert "Open outcomes missing at broker: MSFT" in issues
    assert "Broker positions missing open outcomes: TSLA" in issues

    await _teardown_db()


def test_reconcile_service_report() -> None:
    asyncio.run(_run_reconcile_case())


async def _run_reconcile_without_broker_case() -> None:
    await _setup_db()
    service = ReconcileService(broker=None)
    report = await service.run()
    assert report["source"] == "no_broker"
    assert report["signals_pending"] == 0
    assert report["trades_today"] == 0
    assert report["open_symbols_db"] == []
    assert report["open_symbols_broker"] == []
    assert report["db_truth"]["open_outcomes"] == 0
    assert report["execution_summary"]["executions_today"] == 0
    issues = report["issues"]
    assert isinstance(issues, list)
    assert "No positions and no trades today - verify system is active" in issues
    await _teardown_db()


def test_reconcile_service_without_broker() -> None:
    asyncio.run(_run_reconcile_without_broker_case())
