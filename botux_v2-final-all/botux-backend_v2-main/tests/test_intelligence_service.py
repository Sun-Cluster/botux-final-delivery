from __future__ import annotations

import asyncio
import json
import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from tortoise import Tortoise

from app.services import intelligence_service as intelligence_module
from app.services.intelligence.evaluators import TimeWindowEvaluation
from app.services.intelligence.service import IntelligenceService
from app.services.market.data import MarketDataService
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


class _Broker:
    def __init__(self, *, positions: list[dict[str, object]] | None = None, account: dict[str, object] | None = None) -> None:
        self._positions = positions or []
        self._account = account or {"equity": 100000.0, "cash": 75000.0, "last_equity": 99000.0}

    async def get_quote(self, symbol: str) -> dict[str, object]:
        if symbol.upper() == "SPY":
            return {"symbol": "SPY", "bid": 529.8, "ask": 530.2, "last": 530.0}
        return {"symbol": symbol.upper(), "bid": 99.8, "ask": 100.2, "last": 100.0}

    async def get_positions(self) -> list[dict[str, object]]:
        return list(self._positions)

    async def get_account(self) -> dict[str, object]:
        return dict(self._account)


class _QueueBus:
    def snapshot_sizes(self) -> dict[str, int]:
        return {"signal.process": 0}


class _Container:
    def __init__(self, *, positions: list[dict[str, object]] | None = None, account: dict[str, object] | None = None) -> None:
        self.broker = _Broker(positions=positions, account=account)
        self.queue_bus = _QueueBus()
        self.trading_halted = False
        self.trading_halt_reason = None
        self.trading_halted_at = None


class _DummyModel:
    def __init__(self, probability: float) -> None:
        self._probability = probability

    def predict_proba(self, payload):
        _ = payload
        return [[1.0 - self._probability, self._probability]]


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


async def _run_regime_snapshot_case() -> None:
    async def fake_bars(self, symbol: str, *, range_name: str = "1y"):
        del self, range_name
        if symbol == "SPY":
            return [{"close": 400.0 + index, "volume": 1000000, "timestamp": "2026-05-19T00:00:00+00:00"} for index in range(260)]
        if symbol == "^VIX":
            return [{"close": 18.2, "volume": 0, "timestamp": "2026-05-19T00:00:00+00:00"} for _ in range(30)]
        return []

    original_fetch_bars = MarketDataService.fetch_daily_bars
    MarketDataService.fetch_daily_bars = fake_bars  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().regime_snapshot(_Container())
    finally:
        MarketDataService.fetch_daily_bars = original_fetch_bars  # type: ignore[method-assign]

    assert payload["regime"] == "BULL"
    assert payload["sub_regime"] == "trending"
    assert payload["should_trade"] is True
    assert payload["source"] == "market_regime_engine"


def test_regime_snapshot_uses_market_data_thresholds() -> None:
    asyncio.run(_run_regime_snapshot_case())


async def _run_correlation_block_case() -> None:
    async def fake_bars(self, symbol: str, *, range_name: str = "6mo"):
        del self, range_name
        if symbol in {"AAPL", "JPM", "XOM"}:
            pattern = [100.0, 101.5, 100.8, 102.4, 101.1, 103.2, 102.1, 104.6] * 9
            return [{"close": value, "volume": 1000000, "timestamp": "2026-05-19T00:00:00+00:00"} for value in pattern]
        return [{"close": float(index * 2), "volume": 1000000, "timestamp": "2026-05-19T00:00:00+00:00"} for index in range(100, 170)]

    original_fetch_bars = MarketDataService.fetch_daily_bars
    MarketDataService.fetch_daily_bars = fake_bars  # type: ignore[method-assign]
    try:
            payload = await IntelligenceService().correlation_check(
                "AAPL",
                _Container(positions=[{"symbol": "JPM"}, {"symbol": "XOM"}]),
            )
    finally:
        MarketDataService.fetch_daily_bars = original_fetch_bars  # type: ignore[method-assign]

    assert payload["allowed"] is False
    assert len(payload["correlated_with"]) == 2
    assert "High correlation" in str(payload["reason"])


def test_correlation_check_blocks_when_two_positions_are_highly_correlated() -> None:
    asyncio.run(_run_correlation_block_case())


async def _run_filters_case() -> None:
    async def fake_bars(self, symbol: str, *, range_name: str = "6mo"):
        del self, symbol, range_name
        rows = [{"close": 100.0 + (index * 0.1), "volume": 1000000, "timestamp": "2026-05-19T00:00:00+00:00"} for index in range(30)]
        rows[-1]["volume"] = 500000
        return rows

    async def fake_earnings(self, symbol: str):
        del self, symbol
        return {
            "symbol": "AAPL",
            "near_earnings": True,
            "near": True,
            "days_to_earnings": 2,
            "action": "CLOSE_PROFITABLE",
            "allowed": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    original_fetch_bars = MarketDataService.fetch_daily_bars
    original_time_window = intelligence_module.evaluate_time_window
    original_earnings = IntelligenceService.earnings_check
    MarketDataService.fetch_daily_bars = fake_bars  # type: ignore[method-assign]
    IntelligenceService.earnings_check = fake_earnings  # type: ignore[method-assign]
    intelligence_module.evaluate_time_window = lambda now_utc=None: TimeWindowEvaluation(False, "OPEN_AVOID", "Avoiding first 30 min")
    try:
        payload = await IntelligenceService().filters_check("AAPL", _Container())
    finally:
        MarketDataService.fetch_daily_bars = original_fetch_bars  # type: ignore[method-assign]
        IntelligenceService.earnings_check = original_earnings  # type: ignore[method-assign]
        intelligence_module.evaluate_time_window = original_time_window

    assert payload["allowed"] is False
    assert set(payload["failed_filters"]) >= {"time_of_day", "volume_profile", "event_risk"}


def test_filters_check_uses_time_volume_and_earnings_evaluators() -> None:
    asyncio.run(_run_filters_case())


async def _run_pdt_status_case() -> None:
    await _setup_db()
    now = datetime.now(timezone.utc)
    for index in range(2):
        signal = Signal(
            signal_id=f"signal-{index}",
            symbol=f"AAPL{index}",
            action=OrderAction.BUY,
            score=0.7,
            confidence=0.8,
            source="alpaca_news",
            status=SignalStatus.PENDING,
        )
        await SignalsRepository(connection=None).save_signal(signal)
        await TradeOutcomesRepository(connection=None).save_outcome(
                TradeOutcome(
                    trade_id=f"trade-{index}",
                    signal_id=signal.signal_id,
                    symbol=signal.symbol,
                    outcome=TradeOutcomeStatus.WIN,
                    pnl_pct=2.5,
                    opened_at=now - timedelta(hours=2),
                    closed_at=now - timedelta(hours=1),
                    entry_price=100.0,
                    exit_price=102.5,
                quantity=10.0,
                source="alpaca_news",
            )
        )

    payload = await IntelligenceService().pdt_status(
        _Container(account={"equity": 12000.0, "cash": 12000.0, "last_equity": 11900.0})
    )

    assert payload["day_trades_used"] == 2
    assert payload["can_trade"] is False
    assert payload["day_trades_remaining"] == 1

    await _teardown_db()


def test_pdt_status_counts_real_same_day_closed_outcomes() -> None:
    asyncio.run(_run_pdt_status_case())


async def _run_earnings_check_case() -> None:
    async def fake_fetch_earnings_date(self, symbol: str):
        del self, symbol
        return (datetime.now(timezone.utc).date() + timedelta(days=2)).strftime("%Y-%m-%d")

    original_fetch_earnings_date = MarketDataService.fetch_earnings_date
    MarketDataService.fetch_earnings_date = fake_fetch_earnings_date  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().earnings_check("AAPL")
    finally:
        MarketDataService.fetch_earnings_date = original_fetch_earnings_date  # type: ignore[method-assign]

    assert payload["near_earnings"] is True
    assert payload["action"] == "CLOSE_PROFITABLE"
    assert payload["allowed"] is False


def test_earnings_check_uses_calendar_source() -> None:
    asyncio.run(_run_earnings_check_case())


def _write_ml_artifacts(tmp_path, *, probability: float = 0.82) -> None:
    (tmp_path / "signal_scorer.pkl").write_bytes(pickle.dumps(_DummyModel(probability)))
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {
                "accuracy": 0.63,
                "f1_score": 0.57,
                "trained_at": "2026-05-19T00:00:00+00:00",
                "feature_set_version": "ml-feature-set-v1",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "feature_names.json").write_text(
        json.dumps(["combined_score", "confidence"]),
        encoding="utf-8",
    )


async def _run_ml_status_case(tmp_path) -> None:
    async def fake_performance(self):
        del self
        return {
            "total_trades": 120,
            "wins": 70,
            "losses": 50,
            "win_rate": 70 / 120,
            "avg_pnl_pct": 1.4,
            "total_pnl": 14.0,
            "pnl_values": [1.0, -0.4, 2.0],
            "pnl_volatility": 0.21,
        }

    async def fake_ml_evaluations(self, *, limit: int):
        del self, limit
        return [{"generated_at": "2026-05-19T03:00:00+00:00"}]

    _write_ml_artifacts(tmp_path)
    original_model_dir = IntelligenceService._ml_model_dir
    original_performance = IntelligenceService._performance_summary
    original_ml_evaluations = IntelligenceService._ml_evaluations
    IntelligenceService._ml_model_dir = lambda self: tmp_path  # type: ignore[method-assign]
    IntelligenceService._performance_summary = fake_performance  # type: ignore[method-assign]
    IntelligenceService._ml_evaluations = fake_ml_evaluations  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().ml_status()
    finally:
        IntelligenceService._ml_model_dir = original_model_dir  # type: ignore[method-assign]
        IntelligenceService._performance_summary = original_performance  # type: ignore[method-assign]
        IntelligenceService._ml_evaluations = original_ml_evaluations  # type: ignore[method-assign]

    assert payload["status"] == "trained"
    assert payload["model_loaded"] is True
    assert payload["model_version"] == "ml-feature-set-v1"
    assert payload["accuracy"] == 63.0
    assert payload["f1_score"] == 0.57
    assert payload["score_mode"] == "model_artifact"
    assert payload["artifact_state"]["artifacts_complete"] is True
    assert payload["graduation"]["stage_name"] == "Learner"


def test_ml_status_reports_real_artifact_readiness(tmp_path) -> None:
    asyncio.run(_run_ml_status_case(tmp_path))


async def _run_ml_scores_case(tmp_path) -> None:
    async def fake_performance(self):
        del self
        return {
            "total_trades": 120,
            "wins": 70,
            "losses": 50,
            "win_rate": 70 / 120,
            "avg_pnl_pct": 1.4,
            "total_pnl": 14.0,
            "pnl_values": [1.0, -0.4, 2.0],
            "pnl_volatility": 0.21,
        }

    async def fake_evaluations(self, *, limit: int):
        del self, limit
        return []

    async def fake_perf_by_symbol(self):
        del self
        return {"AAPL": {"win_rate": 0.72, "avg_pnl_pct": 1.8, "trades": 14}}

    async def fake_regime(self, container):
        del self, container
        return {"regime": "BULL", "multiplier": 0.85, "vix": 17.5}

    async def fake_quote(self, container, symbol: str):
        del self, container
        return {"symbol": symbol, "bid": 101.0, "ask": 101.4, "last": 101.2}

    _write_ml_artifacts(tmp_path, probability=0.83)
    original_model_dir = IntelligenceService._ml_model_dir
    original_performance = IntelligenceService._performance_summary
    original_ml_evaluations = IntelligenceService._ml_evaluations
    original_perf_by_symbol = IntelligenceService._performance_by_symbol
    original_regime = IntelligenceService.regime_snapshot
    original_quote = IntelligenceService._safe_quote
    IntelligenceService._ml_model_dir = lambda self: tmp_path  # type: ignore[method-assign]
    IntelligenceService._performance_summary = fake_performance  # type: ignore[method-assign]
    IntelligenceService._ml_evaluations = fake_evaluations  # type: ignore[method-assign]
    IntelligenceService._performance_by_symbol = fake_perf_by_symbol  # type: ignore[method-assign]
    IntelligenceService.regime_snapshot = fake_regime  # type: ignore[method-assign]
    IntelligenceService._safe_quote = fake_quote  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().ml_scores(_Container(), limit=1)
    finally:
        IntelligenceService._ml_model_dir = original_model_dir  # type: ignore[method-assign]
        IntelligenceService._performance_summary = original_performance  # type: ignore[method-assign]
        IntelligenceService._ml_evaluations = original_ml_evaluations  # type: ignore[method-assign]
        IntelligenceService._performance_by_symbol = original_perf_by_symbol  # type: ignore[method-assign]
        IntelligenceService.regime_snapshot = original_regime  # type: ignore[method-assign]
        IntelligenceService._safe_quote = original_quote  # type: ignore[method-assign]

    assert payload["score_mode"] == "model_artifact"
    assert payload["model_loaded"] is True
    assert payload["scores"][0]["source"] == "ml_model_artifact"
    assert payload["scores"][0]["ml_score"] == pytest.approx(0.83, abs=1e-6)


def test_ml_scores_use_model_artifacts_when_available(tmp_path) -> None:
    asyncio.run(_run_ml_scores_case(tmp_path))


async def _run_ml_evaluate_case() -> None:
    captured: list[dict[str, object]] = []

    async def fake_append(self, event_type: str, payload: dict[str, object]):
        del self
        captured.append({"event_type": event_type, "payload": payload})

    async def fake_performance(self):
        del self
        return {
            "total_trades": 18,
            "wins": 10,
            "losses": 8,
            "win_rate": 10 / 18,
            "avg_pnl_pct": 0.8,
            "total_pnl": 4.2,
            "pnl_values": [1.0, -0.5, 0.6],
            "pnl_volatility": 0.18,
        }

    async def fake_evaluations(self, *, limit: int):
        del self, limit
        return []

    async def fake_perf_by_symbol(self):
        del self
        return {"AAPL": {"win_rate": 0.58, "avg_pnl_pct": 1.2, "trades": 9}}

    async def fake_regime(self, container):
        del self, container
        return {"regime": "BULL", "multiplier": 0.8, "vix": 18.0}

    async def fake_quote(self, container, symbol: str):
        del self, container, symbol
        return {"bid": 100.0, "ask": 100.2, "last": 100.1}

    original_append = IntelligenceService._append_artifact
    original_performance = IntelligenceService._performance_summary
    original_ml_evaluations = IntelligenceService._ml_evaluations
    original_perf_by_symbol = IntelligenceService._performance_by_symbol
    original_regime = IntelligenceService.regime_snapshot
    original_quote = IntelligenceService._safe_quote
    IntelligenceService._append_artifact = fake_append  # type: ignore[method-assign]
    IntelligenceService._performance_summary = fake_performance  # type: ignore[method-assign]
    IntelligenceService._ml_evaluations = fake_evaluations  # type: ignore[method-assign]
    IntelligenceService._performance_by_symbol = fake_perf_by_symbol  # type: ignore[method-assign]
    IntelligenceService.regime_snapshot = fake_regime  # type: ignore[method-assign]
    IntelligenceService._safe_quote = fake_quote  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().ml_evaluate("AAPL", _Container())
    finally:
        IntelligenceService._append_artifact = original_append  # type: ignore[method-assign]
        IntelligenceService._performance_summary = original_performance  # type: ignore[method-assign]
        IntelligenceService._ml_evaluations = original_ml_evaluations  # type: ignore[method-assign]
        IntelligenceService._performance_by_symbol = original_perf_by_symbol  # type: ignore[method-assign]
        IntelligenceService.regime_snapshot = original_regime  # type: ignore[method-assign]
        IntelligenceService._safe_quote = original_quote  # type: ignore[method-assign]

    assert payload["score_mode"] == "heuristic_fallback"
    assert "dependencies" in payload
    assert "artifact_state" in payload
    assert "features" in payload
    assert captured[0]["event_type"] == "ml.evaluate"


def test_ml_evaluate_persists_runtime_metadata() -> None:
    asyncio.run(_run_ml_evaluate_case())


async def _run_llm_status_case() -> None:
    async def fake_artifacts(self, *, prefix: str, limit: int):
        del self, prefix, limit
        return [
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "cost_usd": 0.42,
                "call_count_delta": 1,
                "cache_key": "headline:1",
            },
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "cost_usd": 0.18,
                "call_count_delta": 2,
                "cache_key": "headline:2",
            },
        ]

    container = _Container()
    container.config = SimpleNamespace(
        openai_api_key_present=True,
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_fast_model="gpt-4o-mini",
        llm_daily_cap_usd=7.5,
    )
    original_artifacts = IntelligenceService._artifact_rows
    IntelligenceService._artifact_rows = fake_artifacts  # type: ignore[method-assign]
    try:
        payload = await IntelligenceService().llm_status(container)
    finally:
        IntelligenceService._artifact_rows = original_artifacts  # type: ignore[method-assign]

    assert payload["status"] == "active"
    assert payload["enabled"] is True
    assert payload["daily_cost"] == pytest.approx(0.6, abs=1e-6)
    assert payload["call_count"] == 3
    assert payload["cache_size"] == 2
    assert payload["tracking_source"] == "audit_logs"


def test_llm_status_reflects_runtime_activity() -> None:
    asyncio.run(_run_llm_status_case())
