from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app.services.lanes.ausmine import AusmineLaneService
from app.services.intelligence.service import IntelligenceService
from app.services.lanes.options import (
    MAX_PREMIUM_PER_TRADE,
    MIN_SIGNAL_SCORE,
    OptionsLaneService,
    TARGET_DTE_MAX,
    TARGET_DTE_MIN,
)
from app.services.scan.service import ScanService
from app.services.lanes.swingtrade import (
    ATR_STOP_MULTIPLIER,
    BREAKEVEN_DAYS,
    MAX_HOLD_DAYS,
    MIN_HAWK_SCORE,
    PARTIAL_PROFIT_PCT,
    SwingtradeLaneService,
)
from app.services.lanes.tradecopy import MIN_CONSENSUS_BUYS, TradecopyLaneService
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome
from tortoise import Tortoise


# From test_tradecopy_lane_service.py


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


async def _run_tradecopy_lane_case() -> None:
    await _setup_db()

    async with UnitOfWork() as uow:
        await BotsRepository(connection=uow.connection).upsert_bot_profile(
            "copycat",
            {
                "display_name": "Echo",
                "strategy_type": "institutional_replication",
                "market": "us_equities",
                "broker": "alpaca",
                "lifecycle_state": "shadow",
                "enabled": False,
            },
        )

    service = TradecopyLaneService()
    summary = await service.run_scan()

    assert summary["filing_check"]["checked"] == 15
    assert summary["filing_check"]["funds_with_data"] == 15
    assert summary["filing_check"]["new_filings"] == 4
    assert summary["scan_state"] == "completed"
    assert summary["status_code"] == "ok"
    assert summary["watchlist"] == ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
    assert len(summary["candidates"]) >= 3
    assert all(int(candidate["funds_buying"]) >= MIN_CONSENSUS_BUYS for candidate in summary["candidates"])
    assert summary["signals"] == 3

    scan_payload = await ScanService().get_lane_scan(lane="tradecopy")
    assert scan_payload["scan_status"] == "ok"
    assert scan_payload["scan_state"] == "completed"
    assert scan_payload["scan_status_code"] == "ok"
    assert scan_payload["count"] >= 3

    signals = await SignalsRepository(connection=None).list_recent(limit=10)
    tradecopy_signals = [signal for signal in signals if signal.source == "tradecopy"]
    assert len(tradecopy_signals) == 3
    assert all(signal.lane_hint == "tradecopy" for signal in tradecopy_signals)
    assert all(signal.strategy_hint == "institutional_replication" for signal in tradecopy_signals)

    first_signal = tradecopy_signals[0]
    second_signal = tradecopy_signals[1]
    await SignalsRepository(connection=None).save_signal(
        Signal(
            signal_id="tradecopy-extra-closed",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.8,
            source="tradecopy",
            lane_hint="tradecopy",
            strategy_hint="institutional_replication",
            status=SignalStatus.EXECUTED,
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="tradecopy-open-1",
            signal_id=first_signal.signal_id,
            symbol=first_signal.symbol,
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.0,
            entry_price=185.0,
            quantity=12.0,
            bot_id="copycat",
            source="tradecopy",
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="tradecopy-closed-1",
            signal_id="tradecopy-extra-closed",
            symbol=second_signal.symbol,
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=7.25,
            entry_price=130.0,
            exit_price=139.42,
            quantity=10.0,
            close_reason="consensus_break",
            bot_id="copycat",
            source="tradecopy",
        )
    )

    status = await ScanService().get_lane_status(
        lane="tradecopy",
        bot_id="copycat",
        enabled=False,
        lifecycle_state="shadow",
    )

    assert status["tracked_funds"] == 15
    assert status["watchlist"]["count"] == 6
    assert status["funds_with_data"] == 15
    assert status["open_positions"] == 1
    assert status["max_positions"] == 6
    assert status["positions"][first_signal.symbol]["qty"] == 12.0
    assert len(status["consensus_buys"]) >= 3
    assert status["stats"]["opened"] == 2
    assert status["stats"]["closed"] == 1
    assert status["stats"]["total_pnl"] == 7.25
    assert status["config"]["min_consensus"] == 3
    assert status["config"]["position_size"] == "2.5%"
    assert status["config"]["max_hold_days"] == 100
    assert status["fleet_slot_status"] == "paper_only"
    assert status["signals_generated"] == 4
    assert status["scan_state"] == "completed"
    assert status["scan_status_code"] == "ok"

    await _teardown_db()


def test_tradecopy_lane_service_persists_consensus_and_status_truth() -> None:
    asyncio.run(_run_tradecopy_lane_case())


async def _run_tradecopy_lane_live_13f_case() -> None:
    await _setup_db()

    service = TradecopyLaneService()
    live_rows = [
        {
            "fund": "Berkshire Hathaway",
            "cik": "0001067983",
            "weight": 1.5,
            "latest_date": "2026-05-15",
            "new_filing": True,
            "updated_at": "2026-06-11T00:00:00+00:00",
            "holdings_count": 3,
            "holdings": [
                {"issuer": "Apple Inc", "title_of_class": "COM", "reported_value": 150_000_000.0, "reported_shares": 750_000.0},
                {"issuer": "Microsoft Corp", "title_of_class": "COM", "reported_value": 90_000_000.0, "reported_shares": 300_000.0},
            ],
        },
        {
            "fund": "Bridgewater Associates",
            "cik": "0001350694",
            "weight": 1.2,
            "latest_date": "2026-05-15",
            "new_filing": True,
            "updated_at": "2026-06-11T00:00:00+00:00",
            "holdings_count": 2,
            "holdings": [
                {"issuer": "Apple Inc", "title_of_class": "COM", "reported_value": 110_000_000.0, "reported_shares": 550_000.0},
            ],
        },
        {
            "fund": "Renaissance Technologies",
            "cik": "0001037389",
            "weight": 1.3,
            "latest_date": "2026-05-15",
            "new_filing": True,
            "updated_at": "2026-06-11T00:00:00+00:00",
            "holdings_count": 4,
            "holdings": [
                {"issuer": "Apple Inc", "title_of_class": "COM", "reported_value": 95_000_000.0, "reported_shares": 475_000.0},
                {"issuer": "NVIDIA Corp", "title_of_class": "COM", "reported_value": 88_000_000.0, "reported_shares": 220_000.0},
            ],
        },
    ]

    with patch("app.services.intelligence.sec_13f.Sec13FService.fetch_tracked_fund_rows", return_value=live_rows):
        summary = await service.run_scan()

    assert summary["data_mode"] == "live_13f"
    assert summary["filing_check"]["funds_with_data"] == 3
    assert summary["signals"] == 1
    assert summary["candidates"][0]["symbol"] == "AAPL"
    assert summary["candidates"][0]["funds_buying"] == 3
    assert summary["candidates"][0]["reference_price"] == 200.0

    signals = await SignalsRepository(connection=None).list_recent(limit=10)
    tradecopy_signals = [signal for signal in signals if signal.source == "tradecopy"]
    assert len(tradecopy_signals) == 1
    assert float(tradecopy_signals[0].metadata.get("reference_price") or 0.0) == 200.0

    await _teardown_db()


def test_tradecopy_lane_service_prefers_live_13f_data_when_available() -> None:
    asyncio.run(_run_tradecopy_lane_live_13f_case())


# From test_options_lane_service.py


async def _run_options_lane_case() -> None:
    await _setup_db()

    async with UnitOfWork() as uow:
        await BotsRepository(connection=uow.connection).upsert_bot_profile(
            "gambler",
            {
                "display_name": "Prism",
                "strategy_type": "options_premium",
                "market": "us_options",
                "broker": "alpaca",
                "lifecycle_state": "paper",
                "enabled": False,
            },
        )

    seed_signals = [
        Signal(
            signal_id="seed-aapl-call",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.84,
            confidence=0.82,
            source="watchlist_momentum",
            lane_hint="scout",
            strategy_hint="hawk",
            status=SignalStatus.PENDING,
            metadata={"regime": "bull", "rsi": 49},
        ),
        Signal(
            signal_id="seed-nvda-put",
            symbol="NVDA",
            action=OrderAction.SELL,
            score=0.89,
            confidence=0.78,
            source="news",
            lane_hint="scout",
            strategy_hint="macro",
            status=SignalStatus.PENDING,
            metadata={"regime": "neutral", "rsi": 72},
        ),
        Signal(
            signal_id="seed-tsla-low",
            symbol="TSLA",
            action=OrderAction.BUY,
            score=0.79,
            confidence=0.76,
            source="watchlist_momentum",
            lane_hint="scout",
            strategy_hint="hawk",
            status=SignalStatus.PENDING,
        ),
        Signal(
            signal_id="seed-qqq-index",
            symbol="QQQ",
            action=OrderAction.BUY,
            score=0.86,
            confidence=0.81,
            source="watchlist_momentum",
            lane_hint="scout",
            strategy_hint="hawk",
            status=SignalStatus.PENDING,
        ),
    ]
    for signal in seed_signals:
        await SignalsRepository(connection=None).save_signal(signal)

    summary = await OptionsLaneService().run_scan(container=None)

    assert summary["status"] == "ok"
    assert summary["scan_state"] == "completed"
    assert summary["status_code"] == "ok"
    assert summary["regime"] == "NEUTRAL"
    assert summary["signals"] == 2
    assert summary["scanned"] == 28
    assert int(summary["skip_reasons"]["index_hedge_disabled"]) == 2
    assert int(summary["skip_reasons"]["score_below_threshold"]) == 1
    assert len(summary["candidates"]) == 2

    call_candidate = next(candidate for candidate in summary["candidates"] if candidate["underlying"] == "AAPL")
    put_candidate = next(candidate for candidate in summary["candidates"] if candidate["underlying"] == "NVDA")
    assert call_candidate["type"] == "call"
    assert put_candidate["type"] == "put"
    assert MIN_SIGNAL_SCORE <= int(call_candidate["score"]) <= 100
    assert MIN_SIGNAL_SCORE <= int(put_candidate["score"]) <= 100
    assert TARGET_DTE_MIN <= int(call_candidate["dte"]) <= TARGET_DTE_MAX
    assert TARGET_DTE_MIN <= int(put_candidate["dte"]) <= TARGET_DTE_MAX
    assert float(call_candidate["premium"]) <= MAX_PREMIUM_PER_TRADE
    assert float(put_candidate["premium"]) <= MAX_PREMIUM_PER_TRADE

    scan_payload = await ScanService().get_lane_scan(lane="options")
    assert scan_payload["scan_status"] == "ok"
    assert scan_payload["scan_state"] == "completed"
    assert scan_payload["scan_status_code"] == "ok"
    assert scan_payload["count"] == 2

    signals = await SignalsRepository(connection=None).list_recent(limit=20)
    options_signals = [signal for signal in signals if signal.source == "options"]
    assert len(options_signals) == 2
    assert all(signal.lane_hint == "options" for signal in options_signals)
    assert all(signal.strategy_hint == "options_premium" for signal in options_signals)
    assert all(signal.metadata["market"] == "options_us" for signal in options_signals)
    assert all(signal.metadata["order_type"] == "limit" for signal in options_signals)

    first_signal = next(signal for signal in options_signals if signal.metadata["option_position"]["underlying"] == "AAPL")
    second_signal = next(signal for signal in options_signals if signal.metadata["option_position"]["underlying"] == "NVDA")
    first_position = dict(first_signal.metadata["option_position"])
    second_position = dict(second_signal.metadata["option_position"])
    assert first_signal.symbol == first_position["contract"]
    assert second_signal.symbol == second_position["contract"]
    assert first_signal.metadata["limit_price"] > 0
    assert second_signal.metadata["limit_price"] > 0

    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="options-open-1",
            signal_id=first_signal.signal_id,
            symbol=first_signal.symbol,
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=1.12,
            entry_price=2.35,
            quantity=1.0,
            opened_at=datetime.now(timezone.utc) - timedelta(days=4),
            bot_id="gambler",
            source="options",
            features={
                "contract": first_position["contract"],
                "underlying": first_position["underlying"],
                "type": first_position["type"],
                "strike": first_position["strike"],
                "expiration": first_position["expiration"],
                "premium_paid": first_position["premium"],
                "option_position": first_position,
            },
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="options-closed-1",
            signal_id=second_signal.signal_id,
            symbol=second_signal.symbol,
            outcome=TradeOutcomeStatus.LOSS,
            pnl_pct=-0.27,
            entry_price=2.1,
            exit_price=1.45,
            quantity=1.0,
            closed_at=datetime.now(timezone.utc),
            close_reason="stop_loss",
            bot_id="gambler",
            source="options",
            features={
                "contract": second_position["contract"],
                "underlying": second_position["underlying"],
                "type": second_position["type"],
                "strike": second_position["strike"],
                "expiration": second_position["expiration"],
                "premium_paid": second_position["premium"],
                "option_position": second_position,
            },
        )
    )

    status = await ScanService().get_lane_status(
        lane="options",
        bot_id="gambler",
        enabled=False,
        lifecycle_state="paper",
    )

    assert status["open_positions"] == 1
    assert status["max_positions"] == 3
    assert status["premium_deployed"] == round(float(first_position["premium"]), 2)
    assert status["max_allocation"] == 15000.0
    assert status["allocation_pct"] > 0.0
    assert status["stats"]["opened"] == 2
    assert status["stats"]["closed"] == 1
    assert status["stats"]["total_pnl"] == -0.27
    assert status["config"]["min_score"] == 80
    assert status["config"]["max_premium"] == 500
    assert status["config"]["dte_range"] == "30-60"
    assert status["config"]["delta_range"] == "0.30-0.45"
    assert status["config"]["profit_target"] == "+100%"
    assert status["config"]["stop_loss"] == "-50%"
    assert status["config"]["dte_exit"] == 14
    assert status["config"]["max_hold_days"] == 45
    assert status["fleet_slot_status"] == "paper_only"
    assert status["scan_state"] == "completed"
    assert status["scan_status_code"] == "ok"
    assert status["signals_generated"] == 2
    assert status["regime"] == "NEUTRAL"
    assert len(status["exit_watch"]) == 1
    assert status["exit_watch"][0]["reason"] == "take_profit"
    assert first_position["contract"] in status["positions"]
    assert status["positions"][first_position["contract"]]["underlying"] == "AAPL"
    assert status["positions"][first_position["contract"]]["type"] == "call"
    assert status["positions"][first_position["contract"]]["premium_paid"] == round(float(first_position["premium"]), 2)

    await _teardown_db()


def test_options_lane_service_persists_candidates_and_status_truth() -> None:
    asyncio.run(_run_options_lane_case())


# From test_swingtrade_lane_service.py


class _FakeBroker:
    async def get_quote(self, symbol: str) -> dict[str, object]:
        prices = {"AAPL": 190.0, "BLK": 820.0, "META": 210.0, "AMD": 115.0}
        return {"last": prices.get(symbol.upper(), 120.0)}

    async def get_account(self) -> dict[str, object]:
        return {"equity": 100000.0, "buying_power": 120000.0}


class _FakeContainer:
    def __init__(self) -> None:
        self.broker = _FakeBroker()


async def _always_bull(container: object | None = None) -> tuple[str, float]:
    del container
    return "BULL", 1.0


async def _always_allowed(self: IntelligenceService, symbol: str) -> dict[str, object]:
    del self, symbol
    return {"allowed": True, "near_earnings": False}


async def _run_swingtrade_lane_case() -> None:
    await _setup_db()

    async with UnitOfWork() as uow:
        await BotsRepository(connection=uow.connection).upsert_bot_profile(
            "drifter",
            {
                "display_name": "Axon",
                "strategy_type": "swing_momentum",
                "market": "us_equities",
                "broker": "alpaca",
                "lifecycle_state": "paper",
                "enabled": False,
            },
        )

    seed_signals = [
        Signal(
            signal_id="seed-aapl-hawk",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.84,
            confidence=0.82,
            source="watchlist_momentum",
            lane_hint="scout",
            strategy_hint="hawk",
            status=SignalStatus.PENDING,
            metadata={"ml_score": 84},
        ),
        Signal(
            signal_id="seed-meta-hawk",
            symbol="META",
            action=OrderAction.BUY,
            score=0.76,
            confidence=0.74,
            source="watchlist_momentum",
            lane_hint="scout",
            strategy_hint="hawk",
            status=SignalStatus.PENDING,
            metadata={"ml_score": 76},
        ),
    ]
    for signal in seed_signals:
        await SignalsRepository(connection=None).save_signal(signal)

    original_earnings = IntelligenceService.earnings_check
    IntelligenceService.earnings_check = _always_allowed
    try:
        container = _FakeContainer()
        service = SwingtradeLaneService()
        service._regime_snapshot = _always_bull  # type: ignore[method-assign]
        summary = await service.run_scan(container=container)
    finally:
        IntelligenceService.earnings_check = original_earnings

    assert summary["status"] == "ok"
    assert summary["scan_state"] == "completed"
    assert summary["status_code"] == "ok"
    assert summary["regime"] == "BULL"
    assert summary["scanned"] == 72
    assert summary["signals"] == len(summary["candidates"])
    assert summary["signals"] >= 20
    assert int(summary["skip_reasons"]["regime_etf_only"]) == 8
    assert "below_threshold" in summary["skip_reasons"]

    first_candidate = summary["candidates"][0]
    assert int(first_candidate["score"]) >= MIN_HAWK_SCORE
    assert float(first_candidate["atr"]) > 0.0
    assert float(first_candidate["entry_price"]) > float(first_candidate["stop_loss"])
    assert float(first_candidate["take_profit"]) > float(first_candidate["entry_price"])
    assert float(first_candidate["volume_ratio"]) > 0.0
    assert first_candidate["order_type"] == "bracket"
    assert first_candidate["market"] == "us_equities"

    scan_payload = await ScanService().get_lane_scan(lane="swingtrade")
    assert scan_payload["scan_status"] == "ok"
    assert scan_payload["scan_state"] == "completed"
    assert scan_payload["scan_status_code"] == "ok"
    assert scan_payload["count"] == len(summary["candidates"])

    signals = await SignalsRepository(connection=None).list_recent(limit=100)
    swing_signals = [signal for signal in signals if signal.source == "swingtrade"]
    assert len(swing_signals) == len(summary["candidates"])
    assert all(signal.lane_hint == "swingtrade" for signal in swing_signals)
    assert all(signal.strategy_hint == "swing_momentum" for signal in swing_signals)
    assert all(signal.metadata["market"] == "us_equities" for signal in swing_signals)
    assert all(signal.metadata["order_type"] == "bracket" for signal in swing_signals)

    lead_signal = swing_signals[0]
    lead_candidate = dict(lead_signal.metadata["candidate"])
    second_signal = swing_signals[1]
    second_candidate = dict(second_signal.metadata["candidate"])

    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="swing-open-1",
            signal_id=lead_signal.signal_id,
            symbol=lead_signal.symbol,
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=PARTIAL_PROFIT_PCT + 0.02,
            entry_price=float(lead_candidate["entry_price"]),
            quantity=float(lead_candidate["qty"]),
            opened_at=datetime.now(timezone.utc) - timedelta(days=4),
            bot_id="drifter",
            source="swingtrade",
            features={
                "entry_price": lead_candidate["entry_price"],
                "stop_loss": lead_candidate["stop_loss"],
                "take_profit": lead_candidate["take_profit"],
                "atr": lead_candidate["atr"],
                "qty": lead_candidate["qty"],
                "partial_filled": False,
                "sector": lead_candidate["sector"],
            },
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="swing-closed-1",
            signal_id=second_signal.signal_id,
            symbol=second_signal.symbol,
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=0.12,
            entry_price=float(second_candidate["entry_price"]),
            exit_price=round(float(second_candidate["entry_price"]) * 1.12, 2),
            quantity=float(second_candidate["qty"]),
            closed_at=datetime.now(timezone.utc),
            close_reason="partial_profit_then_trail",
            bot_id="drifter",
            source="swingtrade",
            features={
                "entry_price": second_candidate["entry_price"],
                "stop_loss": second_candidate["stop_loss"],
                "take_profit": second_candidate["take_profit"],
                "atr": second_candidate["atr"],
                "qty": second_candidate["qty"],
                "partial_filled": True,
                "trailing_stop": round(float(second_candidate["entry_price"]) * 1.04, 2),
                "sector": second_candidate["sector"],
            },
        )
    )

    status = await ScanService().get_lane_status(
        lane="swingtrade",
        bot_id="drifter",
        enabled=False,
        lifecycle_state="paper",
    )

    assert status["open_positions"] == 1
    assert status["max_positions"] == 8
    assert status["scan_candidates"] == len(summary["candidates"])
    assert status["stats"]["opened"] == 2
    assert status["stats"]["closed"] == 1
    assert status["stats"]["total_pnl"] == 0.12
    assert status["config"]["min_score"] == 55
    assert status["config"]["partial_at"] == "+6%"
    assert status["scan_state"] == "completed"
    assert status["scan_status_code"] == "ok"
    assert status["config"]["trailing_stop"] == "4%"
    assert status["config"]["atr_multiplier"] == ATR_STOP_MULTIPLIER
    assert status["config"]["max_hold_days"] == MAX_HOLD_DAYS
    assert status["config"]["breakeven_days"] == BREAKEVEN_DAYS
    assert status["fleet_slot_status"] == "paper_only"
    assert status["signals_generated"] == len(summary["candidates"])
    assert status["regime"] == "BULL"
    assert lead_signal.symbol in status["positions"]
    assert status["positions"][lead_signal.symbol]["qty"] == float(lead_candidate["qty"])
    assert status["positions"][lead_signal.symbol]["partial_filled"] is False
    assert status["positions"][lead_signal.symbol]["sector"] == str(lead_candidate["sector"])
    assert len(status["exit_watch"]) == 1
    assert status["exit_watch"][0]["reason"] == "take_profit"
    assert sum(status["sector_exposure"].values()) == 1

    await _teardown_db()


def test_swingtrade_lane_service_persists_scan_and_exit_truth() -> None:
    asyncio.run(_run_swingtrade_lane_case())


async def _run_swingtrade_lane_low_buying_power_case() -> None:
    await _setup_db()

    async with UnitOfWork() as uow:
        await BotsRepository(connection=uow.connection).upsert_bot_profile(
            "drifter",
            {
                "display_name": "Axon",
                "strategy_type": "swing_momentum",
                "market": "us_equities",
                "broker": "alpaca",
                "lifecycle_state": "paper",
                "enabled": True,
                "execution_policy": {
                    "capital_basis": "buying_power",
                    "min_buying_power_usd": 100.0,
                    "skip_scan_when_insufficient_buying_power": True,
                },
            },
        )

    class _NoBuyingPowerBroker(_FakeBroker):
        async def get_account(self) -> dict[str, object]:
            return {"equity": 100000.0, "buying_power": 0.0}

    class _NoBuyingPowerContainer:
        def __init__(self) -> None:
            self.broker = _NoBuyingPowerBroker()

    original_earnings = IntelligenceService.earnings_check
    IntelligenceService.earnings_check = _always_allowed
    try:
        service = SwingtradeLaneService()
        service._regime_snapshot = _always_bull  # type: ignore[method-assign]
        summary = await service.run_scan(container=_NoBuyingPowerContainer())
    finally:
        IntelligenceService.earnings_check = original_earnings

    assert summary["status"] == "NO_BUYING_POWER"
    assert summary["status_code"] == "insufficient_buying_power"
    assert summary["scan_state"] == "skipped"
    assert summary["signals"] == 0
    assert summary["buying_power"] == 0.0
    assert summary["capital_base"] == 0.0
    assert "buying power below minimum" in str(summary["gate_reason"]).lower()

    await _teardown_db()


def test_swingtrade_lane_service_skips_when_buying_power_is_exhausted() -> None:
    asyncio.run(_run_swingtrade_lane_low_buying_power_case())


# From test_ausmine_lane_service.py


async def _run_ausmine_lane_case() -> None:
    await _setup_db()

    async with UnitOfWork() as uow:
        await BotsRepository(connection=uow.connection).upsert_bot_profile(
            "nugget_bot",
            {
                "display_name": "Forge",
                "strategy_type": "ausmine_event",
                "market": "asx",
                "broker": "ibkr",
                "lifecycle_state": "paper",
                "enabled": False,
            },
        )

    seed_events = [
        Signal(
            signal_id="seed-bhp-tier-a",
            symbol="BHP",
            action=OrderAction.BUY,
            score=0.82,
            confidence=0.8,
            source="newsfeed_intel",
            headline="BHP Group mining lease approved in Western Australia after environmental approval",
            status=SignalStatus.PENDING,
        ),
        Signal(
            signal_id="seed-ltr-tier-c",
            symbol="LTR",
            action=OrderAction.BUY,
            score=0.78,
            confidence=0.76,
            source="newsfeed_intel",
            headline="Liontown Resources drill results and exploration permit granted in WA",
            status=SignalStatus.PENDING,
        ),
        Signal(
            signal_id="seed-nwh-tier-e",
            symbol="NWH",
            action=OrderAction.BUY,
            score=0.74,
            confidence=0.7,
            source="newsfeed_intel",
            headline="NRW Holdings contract awarded for processing plant and rail project approved in Western Australia",
            status=SignalStatus.PENDING,
        ),
        Signal(
            signal_id="seed-xyz-discovery",
            symbol="XYZ",
            action=OrderAction.BUY,
            score=0.7,
            confidence=0.68,
            source="newsfeed_intel",
            headline="ASX: XYZ mining lease granted in Western Australia for new copper discovery",
            status=SignalStatus.PENDING,
        ),
        Signal(
            signal_id="seed-noise",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.5,
            confidence=0.5,
            source="newsfeed_intel",
            headline="General market update without mining relevance",
            status=SignalStatus.PENDING,
        ),
    ]
    for signal in seed_events:
        await SignalsRepository(connection=None).save_signal(signal)

    service = AusmineLaneService()
    summary = await service.run_scan()

    assert summary["scanned"] >= 5
    assert summary["scan_state"] == "completed"
    assert summary["status_code"] == "ok"
    assert summary["signals"] >= 4
    assert summary["tiers"]["A"] >= 2
    assert summary["tiers"]["C"] >= 1
    assert summary["tiers"]["E"] >= 1
    assert "BHP.AX" in summary["stocks_matched"]
    assert "LTR.AX" in summary["stocks_matched"]
    assert "NWH.AX" in summary["stocks_matched"]
    assert "XYZ.AX" in summary["discovered_tickers"]
    assert int(summary["blocked_reasons"]["no_tier_match"]) >= 1
    assert len(summary["candidates"]) == summary["signals"]

    first_candidate = summary["candidates"][0]
    assert first_candidate["event_family"] in {
        "development_feasibility",
        "production_operations",
        "exploration_discovery",
        "civil_infrastructure",
        "capital_corporate",
        "commodity_sentiment",
    }
    assert float(first_candidate["position_pct"]) > 0.0
    assert str(first_candidate["symbol"]).endswith(".AX")

    scan_payload = await ScanService().get_lane_scan(lane="miner")
    assert scan_payload["scan_status"] == "ok"
    assert scan_payload["scan_state"] == "completed"
    assert scan_payload["scan_status_code"] == "ok"
    assert scan_payload["count"] == len(summary["candidates"])

    signals = await SignalsRepository(connection=None).list_recent(limit=50)
    ausmine_signals = [signal for signal in signals if signal.source == "ausmine"]
    assert len(ausmine_signals) == summary["signals"]
    assert all(signal.lane_hint == "miner" for signal in ausmine_signals)
    assert all(signal.strategy_hint == "ausmine_event" for signal in ausmine_signals)
    assert all(signal.metadata["market"] == "asx_equities" for signal in ausmine_signals)
    assert all(signal.metadata["order_type"] == "limit" for signal in ausmine_signals)

    first_signal = ausmine_signals[0]
    first_candidate_meta = dict(first_signal.metadata["candidate"])
    second_signal = ausmine_signals[1]
    second_candidate_meta = dict(second_signal.metadata["candidate"])

    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="ausmine-open-1",
            signal_id=first_signal.signal_id,
            symbol=first_signal.symbol,
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=0.18,
            entry_price=float(first_candidate_meta["reference_price"]),
            quantity=1500.0,
            bot_id="nugget_bot",
            source="ausmine",
            features={
                "entry_price": first_candidate_meta["reference_price"],
                "qty": 1500.0,
                "tier": first_candidate_meta["tier"],
                "event_type": first_candidate_meta["event_type"],
                "position_pct": first_candidate_meta["position_pct"],
                "state": first_candidate_meta["state"],
                "sector": first_candidate_meta["sector"],
            },
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="ausmine-closed-1",
            signal_id=second_signal.signal_id,
            symbol=second_signal.symbol,
            outcome=TradeOutcomeStatus.WIN,
            pnl_pct=0.31,
            entry_price=float(second_candidate_meta["reference_price"]),
            exit_price=round(float(second_candidate_meta["reference_price"]) * 1.31, 2),
            quantity=1000.0,
            closed_at=datetime.now(timezone.utc),
            close_reason="permit_follow_through",
            bot_id="nugget_bot",
            source="ausmine",
            features={
                "entry_price": second_candidate_meta["reference_price"],
                "qty": 1000.0,
                "tier": second_candidate_meta["tier"],
                "event_type": second_candidate_meta["event_type"],
                "position_pct": second_candidate_meta["position_pct"],
                "state": second_candidate_meta["state"],
                "sector": second_candidate_meta["sector"],
            },
        )
    )

    status = await ScanService().get_lane_status(
        lane="miner",
        bot_id="nugget_bot",
        enabled=False,
        lifecycle_state="paper",
    )

    assert status["bot"] == "Nugget the Prospector"
    assert status["scans_completed"] == 1
    assert status["signals_generated"] == summary["signals"]
    assert status["watchlist"]["total"] >= 20
    assert "XYZ.AX" in status["discovered_tickers"]
    assert status["tiers_last_scan"]["A"] >= 2
    assert int(status["blocked_reasons"]["no_tier_match"]) >= 1
    assert status["open_positions"] == 1
    assert status["stats"]["opened"] == 2
    assert status["stats"]["closed"] == 1
    assert status["stats"]["total_pnl"] == 0.31
    assert status["fleet_slot_status"] == "paper_only"
    assert status["scan_state"] == "completed"
    assert status["scan_status_code"] == "ok"
    assert first_signal.symbol in status["positions"]
    assert status["positions"][first_signal.symbol]["tier"] == first_candidate_meta["tier"]
    assert status["positions"][first_signal.symbol]["state"] == first_candidate_meta["state"]
    assert status["positions"][first_signal.symbol]["position_pct"] == float(first_candidate_meta["position_pct"])

    await _teardown_db()


def test_ausmine_lane_service_persists_classification_tradeability_and_status_truth() -> None:
    asyncio.run(_run_ausmine_lane_case())


async def _run_ausmine_lane_reads_news_article_artifacts_case() -> None:
    await _setup_db()
    article_headline = "BHP Group mining lease approved in Western Australia after environmental approval"

    async with UnitOfWork() as uow:
        await AuditLogsRepository(connection=uow.connection).append(
            event_type="news.article",
            payload={
                "signal_id": "news.article:google_news:BHP:1",
                "source": "google_news",
                "ticker": "BHP",
                "headline": article_headline,
                "confidence": 0.81,
                "sentiment": 0.73,
                "raw_score": 59.13,
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            },
            actor="newsfeed_intel",
        )

    summary = await AusmineLaneService().run_scan()

    assert summary["signals"] >= 1
    assert any(
        str(candidate.get("source_headline", "")) == article_headline
        for candidate in summary["candidates"]
    )

    await _teardown_db()


def test_ausmine_lane_service_reads_recent_news_article_artifacts() -> None:
    asyncio.run(_run_ausmine_lane_reads_news_article_artifacts_case())
