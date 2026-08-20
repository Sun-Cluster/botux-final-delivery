from __future__ import annotations

import asyncio
import threading
from typing import TypeAlias

from domain.enums import OrderAction
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from infra.brokers.alpaca_adapter import AlpacaAdapter
from infra.brokers.base import BrokerPort
from infra.brokers.ibkr_adapter import IbkrAdapter
from infra.brokers.router import BrokerRouter

AdapterCtor: TypeAlias = type[AlpacaAdapter] | type[IbkrAdapter]


def test_broker_adapter_contracts() -> None:
    asyncio.run(_run_contract_suite())


async def _run_contract_suite() -> None:
    for adapter_cls in (AlpacaAdapter, IbkrAdapter):
        await _assert_adapter_contract(adapter_cls)


async def _assert_adapter_contract(adapter_cls: AdapterCtor) -> None:
    adapter: BrokerPort = adapter_cls()

    account = await adapter.get_account()
    assert isinstance(account, dict)
    assert "equity" in account
    assert "cash" in account
    assert "broker" in account

    positions = await adapter.get_positions()
    assert isinstance(positions, list)

    quote = await adapter.get_quote("AAPL")
    assert quote["symbol"] == "AAPL"
    assert "last" in quote

    order_intent = OrderIntent(
        signal_id="sig-contract-1",
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=1.0,
        idempotency_key="idem-contract-1",
    )
    submitted = await adapter.submit_order(order_intent)
    assert submitted["status"] == "submitted"
    broker_order_id = submitted["broker_order_id"]
    assert isinstance(broker_order_id, str)
    assert broker_order_id

    status = await adapter.get_order_status(broker_order_id)
    assert status["status"] == "submitted"
    assert status["broker_order_id"] == broker_order_id

    canceled = await adapter.cancel_order(broker_order_id)
    assert canceled["status"] in {"canceled", "executed", "filled"}
    assert canceled["broker_order_id"] == broker_order_id

    missing = await adapter.get_order_status("missing-order-id")
    assert missing["status"] == "not_found"

    router = BrokerRouter(default_broker=adapter)
    resolved = router.resolve({"symbol": "AAPL"})
    assert resolved is adapter


def test_broker_router_policy_profiles() -> None:
    alpaca = AlpacaAdapter()
    ibkr = IbkrAdapter()
    router = BrokerRouter(default_broker=alpaca, brokers={"alpaca": alpaca, "ibkr": ibkr})

    turbo_route = router.plan(
        Signal(
            signal_id="sig-router-turbo",
            symbol="NVDA",
            action=OrderAction.BUY,
            score=0.9,
            source="turbo",
            lane_hint="turbo",
        )
    )
    assert turbo_route.broker_name == "alpaca"
    assert turbo_route.market == "us_equities"
    assert turbo_route.order_type == "bracket"
    assert turbo_route.bot_id == "turbo"

    copycat_route = router.plan(
        Signal(
            signal_id="sig-router-copycat",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            source="tradecopy",
            lane_hint="copycat",
        )
    )
    assert copycat_route.broker_name == "alpaca"
    assert copycat_route.order_type == "limit"
    assert copycat_route.bot_id == "copycat"

    nugget_route = router.plan(
        Signal(
            signal_id="sig-router-nugget",
            symbol="BHP.AX",
            action=OrderAction.BUY,
            score=0.86,
            source="ausmining",
            lane_hint="miner",
        )
    )
    assert nugget_route.broker_name == "ibkr"
    assert nugget_route.market == "asx_equities"
    assert nugget_route.order_type == "limit"
    assert nugget_route.bot_id == "nugget_bot"

    gambler_route = router.plan(
        Signal(
            signal_id="sig-router-gambler",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.84,
            source="options",
            lane_hint="gambler",
        )
    )
    assert gambler_route.broker_name == "alpaca"
    assert gambler_route.market == "options_us"
    assert gambler_route.order_type == "limit"
    assert gambler_route.bot_id == "gambler"

    drifter_route = router.plan(
        Signal(
            signal_id="sig-router-drifter",
            symbol="META",
            action=OrderAction.BUY,
            score=0.81,
            source="swingtrade",
            lane_hint="drifter",
        )
    )
    assert drifter_route.broker_name == "alpaca"
    assert drifter_route.order_type == "bracket"
    assert drifter_route.bot_id == "drifter"

    evo_route = router.plan(
        Signal(
            signal_id="sig-router-evo",
            symbol="LTR.AX",
            action=OrderAction.BUY,
            score=0.79,
            source="evo_catalyst",
            lane_hint="evo",
        )
    )
    assert evo_route.broker_name == "ibkr"
    assert evo_route.market == "asx_equities"
    assert evo_route.order_type == "market"
    assert evo_route.bot_id == "evo_catalyst"

    evo_us_proxy_route = router.plan(
        Signal(
            signal_id="sig-router-evo-lit",
            symbol="LIT",
            action=OrderAction.BUY,
            score=0.82,
            source="evo_catalyst",
            lane_hint="evo",
            metadata={"market": "us_equities"},
        )
    )
    assert evo_us_proxy_route.broker_name == "ibkr"
    assert evo_us_proxy_route.market == "us_equities"
    assert evo_us_proxy_route.order_type == "market"
    assert evo_us_proxy_route.bot_id == "evo_catalyst"


def test_ibkr_adapter_runs_blocking_calls_on_single_thread_with_event_loop() -> None:
    asyncio.run(_run_ibkr_thread_loop_case())


async def _run_ibkr_thread_loop_case() -> None:
    adapter = IbkrAdapter()
    thread_names: list[str] = []
    loop_ids: list[int] = []

    def capture_thread_context() -> tuple[str, int]:
        thread_names.append(threading.current_thread().name)
        loop_ids.append(id(asyncio.get_event_loop()))
        return thread_names[-1], loop_ids[-1]

    first = await adapter._run_ib_call(capture_thread_context)
    second = await adapter._run_ib_call(capture_thread_context)

    assert first[0].startswith("ibkr")
    assert second[0] == first[0]
    assert second[1] == first[1]
