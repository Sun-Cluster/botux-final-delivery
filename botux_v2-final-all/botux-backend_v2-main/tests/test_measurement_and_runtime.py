from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from tortoise import Tortoise

from app.services.measurement.service import MeasurementService
from app.services.registry.bootstrap import bootstrap_canonical_registry
from db.models import BotProfile, OutboxEvent, SignalRecord, StrategyRegistry
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome
from infra.queue.bus import InProcessQueueBus
from infra.queue.outbox_dispatcher import OutboxDispatcher


def test_outcome_lifecycle_and_measurement_loop() -> None:
    asyncio.run(_run_outcome_lifecycle_and_measurement_loop())


def test_registry_bootstrap_and_outbox_dispatch() -> None:
    asyncio.run(_run_registry_bootstrap_and_outbox_dispatch())


def test_minimal_scorecards_uses_last_7_days_window() -> None:
    asyncio.run(_run_minimal_scorecards_window_case())


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


async def _run_outcome_lifecycle_and_measurement_loop() -> None:
    await _setup_db()
    signals_repo = SignalsRepository(connection=None)
    await signals_repo.save_signal(
        Signal(
            signal_id="tradecopy-alpha-1",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.82,
            status=SignalStatus.EXECUTED,
        )
    )
    # Source is still a persistence field in the new DB model; set it directly here to prove
    # measurement uses DB truth rather than signal_id-only inference.
    signal_record = await SignalRecord.filter(signal_id="tradecopy-alpha-1").first()
    assert signal_record is not None
    signal_record.source = "tradecopy"
    await signal_record.save()

    outcomes_repo = TradeOutcomesRepository(connection=None)
    now = datetime.now(timezone.utc)
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="trade-1",
            signal_id="tradecopy-alpha-1",
            symbol="AAPL",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            opened_at=now,
            entry_price=100.0,
            quantity=2.0,
            bot_id="copycat",
            source="tradecopy",
            features={"last_price": 112.0, "sl_pct": 4.0},
        )
    )
    closed = await outcomes_repo.close_open_outcome(symbol="AAPL", exit_price=112.0, reason="target_hit", closed_at=now)
    assert closed is not None
    assert closed.outcome == "win"
    assert closed.pnl_pct == 12.0
    assert closed.close_reason == "take_profit"

    service = MeasurementService()
    scorecards = await service.scorecards_by_bot(limit=100)
    assert "copycat" in scorecards
    assert scorecards["copycat"]["total_trades"] == 1
    assert scorecards["copycat"]["expectancy_r"] == 3.0
    sources = await service.source_scoreboard(limit=100)
    assert sources["families"]["institutional"]["wins"] == 1
    assert sources["families"]["institutional"]["trust_score"] > 0
    quality = await service.signal_quality_report(window=100)
    assert quality["sources"][0]["source"] == "tradecopy"
    assert quality["sources"][0]["true_positives"] == 1
    await _teardown_db()


async def _run_registry_bootstrap_and_outbox_dispatch() -> None:
    await _setup_db()
    first = await bootstrap_canonical_registry()
    second = await bootstrap_canonical_registry()
    assert first["profiles_written"] >= 5
    assert second["profiles_written"] == 0
    assert await BotProfile.all().count() >= 5
    assert await StrategyRegistry.all().count() >= 5

    bus = InProcessQueueBus()
    signals_repo = SignalsRepository(connection=None)
    await signals_repo.save_signal(
        Signal(
            signal_id="sig-outbox-dispatch",
            symbol="MSFT",
            action=OrderAction.BUY,
            score=0.88,
            status=SignalStatus.PENDING,
        )
    )
    stats = await OutboxDispatcher(bus).dispatch_pending(limit=100)
    assert stats["checked"] >= 1
    assert stats["processed"] >= 1
    assert bus.work_queue.qsize() == 1
    assert await OutboxEvent.filter(status="pending").count() == 0
    await _teardown_db()


async def _run_minimal_scorecards_window_case() -> None:
    await _setup_db()
    signals_repo = SignalsRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)
    now = datetime.now(timezone.utc)

    await signals_repo.save_signal(
        Signal(
            signal_id="scorecard-old-1",
            symbol="MSFT",
            action=OrderAction.BUY,
            score=0.75,
            status=SignalStatus.EXECUTED,
            source="tradecopy",
        )
    )
    await signals_repo.save_signal(
        Signal(
            signal_id="scorecard-recent-1",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.79,
            status=SignalStatus.EXECUTED,
            source="tradecopy",
        )
    )
    await signals_repo.save_signal(
        Signal(
            signal_id="scorecard-open-1",
            symbol="NVDA",
            action=OrderAction.BUY,
            score=0.81,
            status=SignalStatus.EXECUTED,
            source="tradecopy",
        )
    )

    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="old-trade-1",
            signal_id="scorecard-old-1",
            symbol="MSFT",
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=4.0,
            opened_at=now - timedelta(days=10, hours=5),
            closed_at=now - timedelta(days=9),
            entry_price=100.0,
            exit_price=104.0,
            quantity=1.0,
            bot_id="copycat",
            source="tradecopy",
            features={"hold_hours": 29.0, "sl_pct": 2.0},
        )
    )
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="recent-trade-1",
            signal_id="scorecard-recent-1",
            symbol="AAPL",
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=6.0,
            opened_at=now - timedelta(days=2, hours=6),
            closed_at=now - timedelta(days=1),
            entry_price=100.0,
            exit_price=106.0,
            quantity=1.0,
            bot_id="copycat",
            source="tradecopy",
            features={"hold_hours": 30.0, "sl_pct": 2.0},
        )
    )
    await outcomes_repo.save_outcome(
        TradeOutcome(
            trade_id="open-trade-1",
            signal_id="scorecard-open-1",
            symbol="NVDA",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            opened_at=now - timedelta(hours=5),
            entry_price=200.0,
            quantity=1.0,
            bot_id="copycat",
            source="tradecopy",
        )
    )

    scorecards = await MeasurementService().minimal_scorecards_by_bot(days=7, limit=200)
    copycat = scorecards.get("copycat")
    assert copycat is not None
    assert copycat["opened_trades"] == 2
    assert copycat["closed_trades"] == 1
    assert copycat["wins"] == 1
    assert copycat["losses"] == 0
    assert copycat["avg_hold_hours"] == 30.0
    assert copycat["expectancy_r"] == 3.0

    await _teardown_db()
