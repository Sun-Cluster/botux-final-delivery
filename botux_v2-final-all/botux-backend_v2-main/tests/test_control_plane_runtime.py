from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from tortoise import Tortoise

from app.services.control_plane.service import RuntimeControlPlaneService
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome
from infra.brokers.base import BrokerPort
from infra.brokers.router import BrokerRouter
from infra.queue.bus import InProcessQueueBus
from infra.queue.envelope import QueueEnvelope


class FakeControlPlaneBroker(BrokerPort):
    def __init__(self, *, broker_name: str, connected: bool) -> None:
        self.broker_name = broker_name
        self.connected = connected

    async def get_account(self) -> dict:
        if not self.connected:
            return {"broker": self.broker_name, "error": "disconnected", "mode": "paper"}
        return {
            "broker": self.broker_name,
            "equity": 100000,
            "cash": 50000,
            "buying_power": 120000,
            "currency": "USD",
            "mode": "paper",
        }

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "last": 100.0}

    async def submit_order(self, _order_intent) -> dict:
        return {"status": "submitted", "broker_order_id": f"{self.broker_name}-1"}

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "submitted"}


class FakeProcessManager:
    def snapshot(self) -> dict[str, object]:
        return {
            "running": True,
            "active_workers": 2,
            "workers": [{"active": True}, {"active": True}],
            "cpu_pool": {"max_workers": 2},
        }


class FakeScheduler:
    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": True,
            "active": True,
            "job_count": 2,
            "jobs": [{"name": "reconcile.run", "active": True}, {"name": "execution.loop", "active": True}],
        }


@dataclass
class FakeConfig:
    env: str = "test"
    broker_mode: str = "paper"
    reconcile_interval_seconds: int = 60


@dataclass
class FakeContainer:
    config: FakeConfig
    broker_router: BrokerRouter
    queue_bus: InProcessQueueBus
    scheduler: FakeScheduler | None
    last_reconcile_report: dict[str, object] | None
    last_reconcile_run_at: str | None
    trading_halted: bool
    trading_halt_reason: str | None
    process_manager: FakeProcessManager | None


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


async def _run_control_plane_runtime_case() -> None:
    await _setup_db()
    signals_repo = SignalsRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)
    now = datetime.now(timezone.utc)
    await signals_repo.save_signal(
        Signal(
            signal_id="sig-runtime-stale",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            source="scout",
            status=SignalStatus.PENDING,
            created_at=now,
        )
    )
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="manual-open-1",
            signal_id="sig-runtime-stale",
            symbol="AAPL",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
        )
    )

    alpaca = FakeControlPlaneBroker(broker_name="alpaca", connected=True)
    ibkr = FakeControlPlaneBroker(broker_name="ibkr", connected=False)
    router = BrokerRouter(default_broker=alpaca, brokers={"alpaca": alpaca, "ibkr": ibkr})
    queue_bus = InProcessQueueBus()
    await queue_bus.publish_dead_letter(
        QueueEnvelope(
            msg_id="dead-1",
            msg_type="signal.process",
            payload={},
            trace_id="dead-1",
            attempt=3,
            available_at=now,
            last_error="boom",
        )
    )
    container = FakeContainer(
        config=FakeConfig(),
        broker_router=router,
        queue_bus=queue_bus,
        scheduler=FakeScheduler(),
        last_reconcile_report={"issues": ["Broker positions missing open outcomes: TSLA"]},
        last_reconcile_run_at=now.isoformat(),
        trading_halted=False,
        trading_halt_reason=None,
        process_manager=FakeProcessManager(),
    )
    snapshot = await RuntimeControlPlaneService().snapshot(container)

    assert snapshot["status"] == "blocked"
    assert snapshot["high_priority_count"] >= 1
    assert snapshot["db_truth"]["pending_signals"] == 1
    assert snapshot["db_truth"]["open_outcomes"] == 1
    assert snapshot["reconcile"]["issue_count"] == 1
    assert any(duty["duty_id"] == "broker_watchdog" and duty["status"] == "blocked" for duty in snapshot["duties"])
    assert any(duty["duty_id"] == "hygiene" and duty["status"] == "degraded" for duty in snapshot["duties"])

    await _teardown_db()


def test_runtime_control_plane_snapshot_uses_real_runtime_truth() -> None:
    asyncio.run(_run_control_plane_runtime_case())
