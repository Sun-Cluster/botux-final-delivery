from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.lanes.exit import LaneExitService, _broker_position_map, _position_qty


async def _run_tradecopy_exit_ttl_guard_case() -> None:
    service = LaneExitService(exit_guard_ttl_seconds=120)
    container = SimpleNamespace()
    outcomes = [
        {
            "outcome": "open",
            "symbol": "AAPL",
            "opened_at": datetime.now(timezone.utc) - timedelta(days=1),
            "pnl_pct": -999.0,
        }
    ]

    with (
        patch(
            "app.services.lanes.tradecopy.TradecopyLaneService._tradecopy_outcomes",
            new=AsyncMock(return_value=outcomes),
        ),
        patch(
            "app.services.lanes.exit._broker_position_map",
            new=AsyncMock(return_value={"AAPL": {"qty": 2}}),
        ),
        patch(
            "app.services.lanes.exit._has_active_exit_order",
            new=AsyncMock(return_value=False),
        ) as has_active_exit_order_mock,
        patch(
            "app.services.lanes.exit._submit_close_signal",
            new=AsyncMock(return_value=(True, None)),
        ) as submit_close_signal_mock,
        patch(
            "app.services.lanes.exit._append_auto_exit_evidence",
            new=AsyncMock(return_value=None),
        ) as evidence_mock,
    ):
        first = await service.run_tradecopy_exits(container=container)
        second = await service.run_tradecopy_exits(container=container)

    assert first["submitted"] == 1
    assert second["submitted"] == 0
    assert second["skipped"] == 1
    assert submit_close_signal_mock.await_count == 1
    assert has_active_exit_order_mock.await_count == 1
    assert evidence_mock.await_count == 2
    first_call = evidence_mock.await_args_list[0].kwargs
    second_call = evidence_mock.await_args_list[1].kwargs
    assert first_call["result"] == "submitted"
    assert second_call["result"] == "skipped:guard_ttl"
    assert first_call["quantity"] == 2
    assert second_call["quantity"] == 0.0


def test_tradecopy_exit_guard_prevents_duplicate_submit_within_ttl() -> None:
    asyncio.run(_run_tradecopy_exit_ttl_guard_case())


async def _run_tradecopy_exit_open_sell_guard_case() -> None:
    service = LaneExitService(exit_guard_ttl_seconds=120)
    container = SimpleNamespace()
    outcomes = [
        {
            "outcome": "open",
            "symbol": "MSFT",
            "opened_at": datetime.now(timezone.utc) - timedelta(days=1),
            "pnl_pct": -999.0,
        }
    ]

    with (
        patch(
            "app.services.lanes.tradecopy.TradecopyLaneService._tradecopy_outcomes",
            new=AsyncMock(return_value=outcomes),
        ),
        patch(
            "app.services.lanes.exit._broker_position_map",
            new=AsyncMock(return_value={"MSFT": {"qty": 4}}),
        ),
        patch(
            "app.services.lanes.exit._has_active_exit_order",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.lanes.exit._submit_close_signal",
            new=AsyncMock(return_value=(True, None)),
        ) as submit_close_signal_mock,
        patch(
            "app.services.lanes.exit._append_auto_exit_evidence",
            new=AsyncMock(return_value=None),
        ) as evidence_mock,
    ):
        result = await service.run_tradecopy_exits(container=container)

    assert result["submitted"] == 0
    assert result["skipped"] == 1
    assert submit_close_signal_mock.await_count == 0
    assert evidence_mock.await_count == 1
    evidence_call = evidence_mock.await_args_list[0].kwargs
    assert evidence_call["result"] == "skipped:active_exit_order"
    assert evidence_call["reason"] == "stop_loss"


def test_tradecopy_exit_guard_skips_when_active_sell_order_exists() -> None:
    asyncio.run(_run_tradecopy_exit_open_sell_guard_case())


async def _run_broker_position_map_symbol_normalization_case() -> None:
    positions = [
        {"symbol": "BHP.AX", "qty": 3, "currency": "AUD"},
        {"symbol": "RIO", "qty": 2, "currency": "AUD"},
        {"symbol": "AAPL260620C00190000", "qty": 1, "currency": "USD"},
        {"symbol": "AAPL260627P00185000", "qty": 2, "currency": "USD", "underlying_symbol": "AAPL"},
    ]
    container = SimpleNamespace(
        broker=SimpleNamespace(get_positions=AsyncMock(return_value=positions)),
    )

    position_map = await _broker_position_map(container)

    assert _position_qty(position_map.get("BHP.AX")) == 3
    assert _position_qty(position_map.get("BHP")) == 3
    assert _position_qty(position_map.get("RIO")) == 2
    assert _position_qty(position_map.get("RIO.AX")) == 2
    assert _position_qty(position_map.get("AAPL")) == 3
    assert _position_qty(position_map.get("AAPL260620C00190000")) == 1
    assert _position_qty(position_map.get("AAPL260627P00185000")) == 2


def test_broker_position_map_normalizes_asx_and_option_underlying_symbols() -> None:
    asyncio.run(_run_broker_position_map_symbol_normalization_case())
