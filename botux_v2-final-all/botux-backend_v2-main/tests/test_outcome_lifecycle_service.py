from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tortoise import Tortoise

from app.services.outcome.service import OutcomeLifecycleService
from db.models import TradeOutcomeRecord
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


class _NoPositionBroker:
    async def get_positions(self) -> list[dict[str, object]]:
        return []

    async def get_quote(self, symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "last": 120.0}


class _NoPositionBrokerWithFill(_NoPositionBroker):
    async def get_recent_fills(self, *, symbol: str, limit: int = 10) -> list[dict[str, object]]:
        return [
            {"symbol": symbol, "side": "buy", "filled_avg_price": 95.0},
            {"symbol": symbol, "side": "sell", "filled_avg_price": 111.25},
        ]


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


def test_reconcile_open_outcomes_prefers_recent_sell_fill_price() -> None:
    asyncio.run(_run_reconcile_fill_price_priority_case())


def test_reconcile_open_outcomes_uses_broker_quote_when_no_price_on_row() -> None:
    asyncio.run(_run_reconcile_quote_fallback_case())


async def _run_reconcile_fill_price_priority_case() -> None:
    await _setup_db()
    try:
        now = datetime.now(timezone.utc)
        await SignalsRepository(connection=None).save_signal(
            Signal(
                signal_id="sig-open-aapl",
                symbol="AAPL",
                action=OrderAction.BUY,
                score=0.7,
                status=SignalStatus.EXECUTED,
                source="tradecopy",
            )
        )
        await TradeOutcomesRepository(connection=None).save_outcome(
            TradeOutcome(
                trade_id="open-aapl-1",
                signal_id="sig-open-aapl",
                symbol="AAPL",
                outcome=TradeOutcomeStatus.OPEN,
                pnl_pct=0.0,
                opened_at=now,
                entry_price=100.0,
                quantity=1.0,
                bot_id="copycat",
                source="tradecopy",
                features={},
            )
        )
        result = await OutcomeLifecycleService(broker=_NoPositionBrokerWithFill()).reconcile_open_outcomes()

        assert result["checked"] == 1
        assert result["closed_count"] == 1
        assert result["orphan_open"] == []
        assert result["fills_checked"] == 1
        assert result["quotes_checked"] == 0
        row = await TradeOutcomeRecord.filter(symbol="AAPL").first()
        assert row is not None
        assert row.exit_price == 111.25
        assert row.outcome in {"win", "loss"}
        assert row.close_reason == "broker_reconcile"
    finally:
        await _teardown_db()


async def _run_reconcile_quote_fallback_case() -> None:
    await _setup_db()
    try:
        now = datetime.now(timezone.utc)
        await SignalsRepository(connection=None).save_signal(
            Signal(
                signal_id="sig-open-msft",
                symbol="MSFT",
                action=OrderAction.BUY,
                score=0.7,
                status=SignalStatus.EXECUTED,
                source="tradecopy",
            )
        )
        await TradeOutcomesRepository(connection=None).save_outcome(
            TradeOutcome(
                trade_id="open-msft-1",
                signal_id="sig-open-msft",
                symbol="MSFT",
                outcome=TradeOutcomeStatus.OPEN,
                pnl_pct=0.0,
                opened_at=now,
                entry_price=None,
                quantity=1.0,
                bot_id="copycat",
                source="tradecopy",
                features={},
            )
        )
        result = await OutcomeLifecycleService(broker=_NoPositionBroker()).reconcile_open_outcomes()

        assert result["checked"] == 1
        assert result["closed_count"] == 1
        assert result["orphan_open"] == []
        assert result["fills_checked"] == 0
        assert result["quotes_checked"] == 1
        row = await TradeOutcomeRecord.filter(symbol="MSFT").first()
        assert row is not None
        assert row.exit_price == 120.0
    finally:
        await _teardown_db()
