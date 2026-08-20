from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Iterator

from tortoise import Tortoise

from app.services.execution.service import ExecutionService
from app.services.registry.bootstrap import bootstrap_canonical_registry
from app.services.runtime_config.bootstrap import bootstrap_runtime_controls
from app.services.runtime_config.service import RuntimeConfigService
from app.usecases.submit_order import submit_order
from db.models import CouncilDecisionRecord, ExecutionRecord, OrderRecord, SignalRecord, SystemConfig, TradeOutcomeRecord
from db.repositories.signals_repo import SignalsRepository
from db.repositories.system_configs_repo import SystemConfigsRepository
from db.uow import UnitOfWork
from domain.enums import ExecutionStatus, OrderAction, OrderStatus, SignalStatus, TradeOutcomeStatus
from domain.models.execution_result import ExecutionResult
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from infra.brokers.base import BrokerPort


class FakeExecutionService:
    async def submit(self, order_id: str, order_intent: OrderIntent) -> ExecutionResult:
        return ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.FILLED,
            broker_order_id="exec-filled-1",
            filled_qty=order_intent.quantity,
            avg_price=101.25,
        )


class WideSpreadBroker(BrokerPort):
    def __init__(self) -> None:
        self.submit_calls = 0

    async def get_account(self) -> dict:
        return {"equity": 100000.0, "last_equity": 100000.0, "cash": 50000.0}

    async def get_positions(self) -> list[dict]:
        return []

    async def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "bid": 100.0, "ask": 102.0}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        self.submit_calls += 1
        return {
            "status": "filled",
            "broker_order_id": f"wide-{self.submit_calls}",
            "filled_qty": order_intent.quantity,
            "avg_price": 101.0,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled"}


class RiskGateBroker(BrokerPort):
    def __init__(self, *, account: dict[str, object], positions: list[dict], quote: dict[str, object]) -> None:
        self._account = account
        self._positions = positions
        self._quote = quote
        self.submit_calls = 0

    async def get_account(self) -> dict:
        return dict(self._account)

    async def get_positions(self) -> list[dict]:
        return list(self._positions)

    async def get_quote(self, symbol: str) -> dict:
        return {**self._quote, "symbol": symbol}

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        self.submit_calls += 1
        return {
            "status": "filled",
            "broker_order_id": f"risk-{self.submit_calls}",
            "filled_qty": order_intent.quantity,
            "avg_price": 100.0,
        }

    async def cancel_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled"}


def test_runtime_config_db_override_beats_env_fallback() -> None:
    asyncio.run(_run_runtime_config_db_override_case())


def test_submit_order_council_required_and_bypass_parity() -> None:
    asyncio.run(_run_submit_order_council_parity_case())


def test_execution_service_spread_guard_parity() -> None:
    asyncio.run(_run_execution_service_spread_guard_case())


def test_bootstrap_runtime_controls_seeds_effective_env_values() -> None:
    asyncio.run(_run_bootstrap_runtime_controls_seed_case())


def test_execution_service_pdt_and_concentration_gates_follow_prior_intent() -> None:
    asyncio.run(_run_execution_service_pdt_and_concentration_case())


def test_execution_service_daily_cap_and_duplicate_entry_gates_follow_prior_intent() -> None:
    asyncio.run(_run_execution_service_daily_cap_and_duplicate_entry_case())


def test_execution_service_blocks_when_buying_power_is_exhausted() -> None:
    asyncio.run(_run_execution_service_buying_power_case())


def test_news_signal_ownership_mapping_unblocks_profile_gate() -> None:
    asyncio.run(_run_news_signal_ownership_mapping_case())


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


@contextmanager
def _patched_env(**updates: str | None) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _set_runtime_config(key: str, value: object, *, value_type: str | None = None) -> None:
    async with UnitOfWork() as uow:
        repo = SystemConfigsRepository(connection=uow.connection)
        existing = await repo.get_by_key(key)
        resolved_type = value_type or ("bool" if isinstance(value, bool) else "float" if isinstance(value, float) else "int")
        description = None if existing is None else str(existing.get("description") or "")
        await repo.upsert(
            key=key,
            value=value,  # type: ignore[arg-type]
            value_type=resolved_type,
            description=description,
            updated_by="test",
        )


async def _run_runtime_config_db_override_case() -> None:
    await _setup_db()
    try:
        await _set_runtime_config("bypass.council", True, value_type="bool")
        service = RuntimeConfigService()
        bypass_council = await service.resolve_bool("bypass.council")
        assert bypass_council.value is True
        assert bypass_council.origin == "db"

        max_daily_loss = await service.resolve_float("risk.max_daily_loss_pct")
        assert max_daily_loss.value == 0.03
        assert max_daily_loss.origin == "default"
    finally:
        await _teardown_db()


async def _run_submit_order_council_parity_case() -> None:
    await _setup_db()
    try:
        await bootstrap_canonical_registry()
        repo = SignalsRepository(connection=None)

        await _set_runtime_config("bypass.council", False, value_type="bool")
        signal_rejected = Signal(
            signal_id="sig-council-required",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.2,
            source="turbo",
            lane_hint="turbo",
            strategy_hint="turbo",
            status=SignalStatus.PENDING,
        )
        await repo.save_signal(signal_rejected)
        rejected = await submit_order(signal_rejected, execution_service=FakeExecutionService())
        assert rejected is None
        stored_rejected = await repo.get_by_signal_id("sig-council-required")
        assert stored_rejected is not None
        assert stored_rejected.status == SignalStatus.REJECTED

        await _set_runtime_config("bypass.council", True, value_type="bool")
        signal_bypassed = Signal(
            signal_id="sig-council-bypassed",
            symbol="MSFT",
            action=OrderAction.BUY,
            score=0.2,
            source="turbo",
            lane_hint="turbo",
            strategy_hint="turbo",
            status=SignalStatus.PENDING,
        )
        await repo.save_signal(signal_bypassed)
        filled = await submit_order(signal_bypassed, execution_service=FakeExecutionService())
        assert filled is not None
        assert filled.status == ExecutionStatus.FILLED
        stored_bypassed = await repo.get_by_signal_id("sig-council-bypassed")
        assert stored_bypassed is not None
        assert stored_bypassed.status == SignalStatus.EXECUTED
        assert await OrderRecord.filter(signal__signal_id="sig-council-bypassed").count() == 1
        bypass_decision = await CouncilDecisionRecord.filter(signal__signal_id="sig-council-bypassed").first()
        assert bypass_decision is not None
        assert bypass_decision.reason == "bypass_council"
    finally:
        await _teardown_db()


async def _run_news_signal_ownership_mapping_case() -> None:
    await _setup_db()
    try:
        await bootstrap_canonical_registry()
        repo = SignalsRepository(connection=None)

        await _set_runtime_config("bypass.council", True, value_type="bool")
        signal = Signal(
            signal_id="sig-newsapi-owned",
            symbol="NVDA",
            action=OrderAction.BUY,
            score=0.72,
            confidence=0.81,
            source="newsapi",
            lane_hint="news",
            strategy_hint="newsfeed_intel",
            metadata={
                "origin_bot_id": "newsfeed_intel",
                "execution_bot_id": "turbo",
                "bot_id": "turbo",
            },
            status=SignalStatus.PENDING,
        )
        await repo.save_signal(signal)
        filled = await submit_order(signal, execution_service=FakeExecutionService())
        assert filled is not None
        assert filled.status == ExecutionStatus.FILLED
        stored = await repo.get_by_signal_id("sig-newsapi-owned")
        assert stored is not None
        assert stored.status == SignalStatus.EXECUTED
        order = await OrderRecord.filter(signal__signal_id="sig-newsapi-owned").first()
        assert order is not None
        assert order.bot_id == "turbo"
    finally:
        await _teardown_db()


async def _run_execution_service_spread_guard_case() -> None:
    await _setup_db()
    try:
        broker = WideSpreadBroker()
        service = ExecutionService(broker=broker)
        order = OrderIntent(
            signal_id="spread-guard-signal",
            symbol="BHP.AX",
            action=OrderAction.BUY,
            quantity=1.0,
            idempotency_key="spread-guard-order",
            broker_name="ibkr",
            market="asx_equities",
            order_type="limit",
        )

        await _set_runtime_config("execution.enforce_exec_guards", True, value_type="bool")
        await _set_runtime_config("execution.max_spread_bps", 50.0, value_type="float")
        blocked = await service.submit("spread-order-1", order.model_copy(deep=True))
        assert blocked.status == ExecutionStatus.REJECTED
        assert broker.submit_calls == 0

        await _set_runtime_config("execution.enforce_exec_guards", False, value_type="bool")
        allowed = await service.submit("spread-order-2", order.model_copy(deep=True))
        assert allowed.status == ExecutionStatus.FILLED
        assert broker.submit_calls == 1
    finally:
        await _teardown_db()


async def _run_bootstrap_runtime_controls_seed_case() -> None:
    await _setup_db()
    try:
        result = await bootstrap_runtime_controls()
        assert result["seeded"] >= 1

        configs = {row.key: row for row in await SystemConfig.all()}
        assert configs["risk.max_daily_loss_pct"].value == 0.03
        assert configs["risk.risk_per_trade_pct"].value == 0.01
        assert configs["risk.max_position_pct"].value == 0.10
        assert configs["risk.max_open_positions"].value == 15
        assert configs["bypass.council"].value is False
        assert configs["execution.enforce_exec_guards"].value is False
        assert configs["intel.sec_13f_user_agent"].value == "BOTUX tradecopy support@example.com"
        assert configs["intel.sec_13f_timeout_seconds"].value == 8.0
        assert configs["intel.sec_13f_concurrency"].value == 3
        assert configs["intel.sec_13f_new_filing_lookback_days"].value == 7

        resolved = await RuntimeConfigService().resolve_float("risk.max_daily_loss_pct")
        assert resolved.value == 0.03
        assert resolved.origin == "db"
        sec_timeout = await RuntimeConfigService().resolve_float("intel.sec_13f_timeout_seconds")
        assert sec_timeout.value == 8.0
        assert sec_timeout.origin == "db"
    finally:
        await _teardown_db()


async def _run_execution_service_pdt_and_concentration_case() -> None:
    await _setup_db()
    try:
        await _set_runtime_config("bypass.market_hours", True, value_type="bool")
        await _set_runtime_config("execution.enforce_exec_guards", False, value_type="bool")

        pdt_broker = RiskGateBroker(
            account={"equity": 20000.0, "last_equity": 20000.0, "cash": 10000.0, "daytrade_count": 3},
            positions=[],
            quote={"bid": 100.0, "ask": 100.01},
        )
        pdt_service = ExecutionService(broker=pdt_broker)
        pdt_order = OrderIntent(
            signal_id="pdt-gate-signal",
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=1.0,
            idempotency_key="pdt-gate-order",
            broker_name="alpaca",
            market="us_equities",
            order_type="market",
        )
        pdt_result = await pdt_service.submit("pdt-order-1", pdt_order)
        assert pdt_result.status == ExecutionStatus.REJECTED
        assert pdt_broker.submit_calls == 0

        concentration_broker = RiskGateBroker(
            account={"equity": 100000.0, "last_equity": 100000.0, "cash": 40000.0, "daytrade_count": 0},
            positions=[{"symbol": "BHP.AX", "market_value": 9500.0, "qty": 95}],
            quote={"bid": 99.5, "ask": 100.0},
        )
        concentration_service = ExecutionService(broker=concentration_broker)
        concentration_order = OrderIntent(
            signal_id="cap-gate-signal",
            symbol="BHP.AX",
            action=OrderAction.BUY,
            quantity=10.0,
            idempotency_key="cap-gate-order",
            broker_name="ibkr",
            market="asx_equities",
            order_type="limit",
        )
        cap_result = await concentration_service.submit("cap-order-1", concentration_order)
        assert cap_result.status == ExecutionStatus.REJECTED
        assert concentration_broker.submit_calls == 0
    finally:
        await _teardown_db()


async def _run_execution_service_daily_cap_and_duplicate_entry_case() -> None:
    await _setup_db()
    try:
        await _set_runtime_config("bypass.market_hours", True, value_type="bool")
        await _set_runtime_config("execution.enforce_exec_guards", False, value_type="bool")
        await _set_runtime_config("execution.max_trades_per_day", 1, value_type="int")
        await _set_runtime_config("execution.max_trades_per_day_paper", 3, value_type="int")
        await _set_runtime_config("execution.cooldown_minutes", 20, value_type="int")

        now = _utcnow()
        prior_signal = await SignalRecord.create(
            signal_id="prior-live-entry",
            symbol="AAPL",
            action="buy",
            status=SignalStatus.EXECUTED.value,
            source="turbo",
            created_at=now,
        )
        prior_order = await OrderRecord.create(
            signal=prior_signal,
            idempotency_key="prior-live-entry:order",
            symbol="AAPL",
            action="buy",
            quantity=1,
            broker_name="alpaca",
            market="us_equities",
            order_type="market",
            status=OrderStatus.EXECUTED.value,
            created_at=now,
        )
        await ExecutionRecord.create(
            order=prior_order,
            broker_order_id="live-cap-1",
            status=ExecutionStatus.EXECUTED.value,
            filled_qty=1,
            avg_price=100,
            created_at=now,
        )

        live_broker = RiskGateBroker(
            account={"equity": 100000.0, "last_equity": 100000.0, "cash": 50000.0, "mode": "live"},
            positions=[],
            quote={"bid": 100.0, "ask": 100.02},
        )
        live_service = ExecutionService(broker=live_broker)
        live_order = OrderIntent(
            signal_id="live-cap-signal",
            symbol="MSFT",
            action=OrderAction.BUY,
            quantity=1.0,
            idempotency_key="live-cap-order",
            broker_name="alpaca",
            market="us_equities",
            order_type="market",
        )
        live_result = await live_service.submit("live-cap-order-1", live_order)
        assert live_result.status == ExecutionStatus.REJECTED
        assert live_broker.submit_calls == 0

        paper_broker = RiskGateBroker(
            account={"equity": 100000.0, "last_equity": 100000.0, "cash": 50000.0, "mode": "paper"},
            positions=[],
            quote={"bid": 100.0, "ask": 100.02},
        )
        paper_service = ExecutionService(broker=paper_broker)
        paper_order = OrderIntent(
            signal_id="paper-cap-signal",
            symbol="NVDA",
            action=OrderAction.BUY,
            quantity=1.0,
            idempotency_key="paper-cap-order",
            broker_name="alpaca",
            market="us_equities",
            order_type="market",
        )
        paper_result = await paper_service.submit("paper-cap-order-1", paper_order)
        assert paper_result.status == ExecutionStatus.FILLED
        assert paper_broker.submit_calls == 1

        open_signal = await SignalRecord.create(
            signal_id="open-trade-signal",
            symbol="BHP.AX",
            action="buy",
            status=SignalStatus.EXECUTED.value,
            source="ausmine",
            created_at=now,
        )
        open_order = await OrderRecord.create(
            signal=open_signal,
            idempotency_key="open-trade:order",
            symbol="BHP.AX",
            action="buy",
            quantity=10,
            broker_name="ibkr",
            market="asx_equities",
            order_type="limit",
            status=OrderStatus.EXECUTED.value,
            created_at=now,
        )
        await TradeOutcomeRecord.create(
            signal=open_signal,
            order=open_order,
            symbol="BHP.AX",
            outcome=TradeOutcomeStatus.OPEN.value,
            pnl_pct=0.0,
            trade_id="open-bhp-1",
            action="buy",
            quantity=10,
            entry_price=45.0,
            created_at=now,
            closed_at=None,
        )

        duplicate_broker = RiskGateBroker(
            account={"equity": 100000.0, "last_equity": 100000.0, "cash": 50000.0, "mode": "paper"},
            positions=[],
            quote={"bid": 45.0, "ask": 45.05},
        )
        duplicate_service = ExecutionService(broker=duplicate_broker)
        duplicate_order = OrderIntent(
            signal_id="duplicate-open-signal",
            symbol="BHP.AX",
            action=OrderAction.BUY,
            quantity=1.0,
            idempotency_key="duplicate-open-order",
            broker_name="ibkr",
            market="asx_equities",
            order_type="limit",
        )
        duplicate_result = await duplicate_service.submit("duplicate-open-order-1", duplicate_order)
        assert duplicate_result.status == ExecutionStatus.REJECTED
        assert duplicate_broker.submit_calls == 0
    finally:
        await _teardown_db()


async def _run_execution_service_buying_power_case() -> None:
    await _setup_db()
    try:
        await _set_runtime_config("bypass.market_hours", True, value_type="bool")
        await _set_runtime_config("execution.enforce_exec_guards", False, value_type="bool")

        broker = RiskGateBroker(
            account={
                "equity": 100000.0,
                "last_equity": 100000.0,
                "cash": -250000.0,
                "buying_power": 0.0,
                "mode": "paper",
            },
            positions=[],
            quote={"bid": 100.0, "ask": 100.05},
        )
        service = ExecutionService(broker=broker)
        order = OrderIntent(
            signal_id="buying-power-signal",
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=5.0,
            idempotency_key="buying-power-order",
            broker_name="alpaca",
            market="us_equities",
            order_type="market",
        )
        result = await service.submit("buying-power-order-1", order)
        assert result.status == ExecutionStatus.REJECTED
        assert result.error_reason == "insufficient_buying_power:0.00<500.25"
        assert broker.submit_calls == 0
    finally:
        await _teardown_db()


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
