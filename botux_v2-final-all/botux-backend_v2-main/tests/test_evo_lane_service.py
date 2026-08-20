from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tortoise import Tortoise

from app.services.lanes.evo_catalyst import EvoCatalystLaneService
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


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


async def _run_evo_lane_scan_and_status_case() -> None:
    await _setup_db()
    await AuditLogsRepository(connection=None).append(
        event_type="news.article",
        actor="newsfeed_intel",
        payload={
            "signal_id": "news.article:asx_announcement:MIN.AX:001",
            "source": "asx_announcement",
            "ticker": "MIN.AX",
            "headline": "Mineral Resources secures binding lithium offtake and processing expansion approval",
            "symbols": ["MIN.AX"],
            "sentiment": 0.74,
            "confidence": 0.86,
            "is_price_sensitive": True,
            "raw_score": 48.0,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await AuditLogsRepository(connection=None).append(
        event_type="news.article",
        actor="newsfeed_intel",
        payload={
            "signal_id": "news.article:alpaca_news:PLS.AX:002",
            "source": "alpaca_news",
            "ticker": "PLS.AX",
            "headline": "Pilbara Minerals rallies on strategic battery metals partnership and supply agreement",
            "symbols": ["PLS.AX"],
            "sentiment": 0.63,
            "confidence": 0.8,
            "is_price_sensitive": True,
            "raw_score": 36.0,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    service = EvoCatalystLaneService()
    summary = await service.run_scan()

    assert summary["status"] == "ok"
    assert summary["scan_state"] == "completed"
    assert int(summary["signals"]) > 0
    assert any(candidate["symbol"] in {"MIN.AX", "PLS.AX"} for candidate in summary["candidates"])

    signals = await SignalsRepository(connection=None).list_recent(limit=20)
    evo_signals = [row for row in signals if row.source == "evo_catalyst"]
    assert len(evo_signals) == int(summary["signals"])
    assert all(row.lane_hint == "evo_catalyst" for row in evo_signals)
    assert all(row.strategy_hint == "evo_catalyst_event" for row in evo_signals)

    first_signal = evo_signals[0]
    helper_signal = Signal(
        signal_id="evo-helper-closed",
        symbol=first_signal.symbol,
        action=OrderAction.BUY,
        score=0.82,
        source="evo_catalyst",
        lane_hint="evo_catalyst",
        strategy_hint="evo_catalyst_event",
        status=SignalStatus.EXECUTED,
    )
    await SignalsRepository(connection=None).save_signal(helper_signal)

    outcomes = TradeOutcomesRepository(connection=None)
    await outcomes.save_outcome(
        TradeOutcome(
            trade_id="evo-open-1",
            signal_id=first_signal.signal_id,
            symbol=first_signal.symbol,
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            entry_price=12.5,
            quantity=100.0,
            bot_id="evo_catalyst",
            source="evo_catalyst",
        )
    )
    await outcomes.save_outcome(
        TradeOutcome(
            trade_id="evo-closed-1",
            signal_id=helper_signal.signal_id,
            symbol=first_signal.symbol,
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=4.8,
            entry_price=10.0,
            exit_price=10.48,
            quantity=80.0,
            bot_id="evo_catalyst",
            source="evo_catalyst",
            closed_at=datetime.now(timezone.utc),
        )
    )

    status = await service.get_status(
        bot_id="evo_catalyst",
        enabled=True,
        lifecycle_state="paper",
    )
    assert status["lane"] == "evo_catalyst"
    assert status["status"] == "active"
    assert status["open_positions"] == 1
    assert status["stats"]["closed"] == 1
    assert status["scan_state"] == "completed"
    assert status["scan_candidates"] >= 1

    await _teardown_db()


def test_evo_lane_service_scan_and_status() -> None:
    asyncio.run(_run_evo_lane_scan_and_status_case())
