from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from tortoise import Tortoise

from api.routers.control_plane_compat import governance_registry
from api.routers.api_extra import _update_strategy_lifecycle
from app.services.governance.service import GovernanceService
from app.services.registry.bootstrap import bootstrap_canonical_registry
from db.models import AuditLog, StrategyRegistry
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from domain.enums import OrderAction, SignalStatus, TradeOutcomeStatus
from domain.models.signal import Signal
from domain.models.trade_outcome import TradeOutcome


def test_governance_reports_and_lifecycle_evidence() -> None:
    asyncio.run(_run_governance_reports_and_lifecycle_evidence())


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


async def _run_governance_reports_and_lifecycle_evidence() -> None:
    await _setup_db()
    await bootstrap_canonical_registry()
    await _seed_strategy_activity()

    service = GovernanceService()
    shadow = await service.strategy_shadow_report(persist=True)
    shadow_rows = {str(item["strategy_id"]): item for item in shadow["strategies"]}
    assert shadow_rows["strat_copycat_v1"]["matched_signals"] >= 5
    assert shadow_rows["strat_copycat_v1"]["coverage"] > 0

    readiness = await service.promotion_readiness_report(persist=True)
    items = {str(item["strategy_id"]): item for item in readiness["items"]}
    assert items["strat_turbo_v1"]["candidacy"] == "promotable"
    assert items["strat_turbo_v1"]["ready"] is True
    assert items["strat_copycat_v1"]["candidacy"] == "reject-candidacy"
    assert items["strat_copycat_v1"]["decay_severity"] in {"WATCH", "WARNING", "ALERT"}

    updated = await _update_strategy_lifecycle("strat_turbo_v1", lifecycle_state="live", action="promoted")
    assert updated["updated"] is True
    assert updated["evidence"]["candidacy"] == "promotable"

    registry_payload = await governance_registry()
    units = {str(item["technical_id"]): item for item in registry_payload["units"]}
    assert units["strat_turbo_v1"]["candidacy"] == "hold"
    assert "governance" in registry_payload["strategies"]["strat_turbo_v1"]
    registry_governance = registry_payload["strategies"]["strat_turbo_v1"]["governance"]
    assert registry_governance["last_action"] == "promoted"
    assert registry_governance["last_action_evidence"]["candidacy"] == "promotable"

    strategy = await StrategyRegistry.filter(strategy_id="strat_turbo_v1").first()
    assert strategy is not None
    metadata = strategy.metadata if isinstance(strategy.metadata, dict) else {}
    governance_state = metadata.get("governance_state")
    assert isinstance(governance_state, dict)
    assert governance_state["last_action"] == "promoted"
    evidence = governance_state["evidence"]
    assert evidence["candidacy"] == "promotable"

    shadow_events = await AuditLog.filter(event_type="strategy_shadow_metrics").count()
    readiness_events = await AuditLog.filter(event_type="promotion_readiness").count()
    lifecycle_events = await AuditLog.filter(event_type="strategy_lifecycle_action").count()
    assert shadow_events >= 1
    assert readiness_events >= 1
    assert lifecycle_events == 1

    await _teardown_db()


async def _seed_strategy_activity() -> None:
    signals_repo = SignalsRepository(connection=None)
    outcomes_repo = TradeOutcomesRepository(connection=None)
    now = datetime.now(timezone.utc)

    for index in range(35):
        signal_id = f"turbo-signal-{index}"
        created_at = now - timedelta(days=40 - index)
        await signals_repo.save_signal(
            Signal(
                signal_id=signal_id,
                symbol="AAPL",
                action=OrderAction.BUY,
                score=0.82,
                confidence=0.78,
                source="scout",
                lane_hint="news",
                strategy_hint="watchlist_momentum",
                status=SignalStatus.EXECUTED,
                created_at=created_at,
                metadata={"bot_id": "turbo"},
            )
        )
        pnl = 2.4 if index < 24 else -0.8
        outcome = TradeOutcomeStatus.WIN if pnl > 0 else TradeOutcomeStatus.LOSS
        await outcomes_repo.save_outcome(
            TradeOutcome(
                trade_id=f"turbo-trade-{index}",
                signal_id=signal_id,
                symbol="AAPL",
                outcome=outcome,
                pnl_pct=pnl,
                opened_at=created_at,
                closed_at=created_at + timedelta(hours=6),
                entry_price=100.0,
                exit_price=102.0 if pnl > 0 else 99.2,
                quantity=1.0,
                bot_id="turbo",
                source="scout",
                features={
                    "bot_id": "turbo",
                    "source": "scout",
                    "r_multiple": 1.2 if pnl > 0 else -0.4,
                    "sl_pct": 2.0,
                    "hold_hours": 6.0,
                    "regime": "BULL" if index % 2 == 0 else "NEUTRAL",
                },
            )
        )

    for index in range(20):
        signal_id = f"copycat-signal-{index}"
        created_at = now - timedelta(days=20 - index)
        await signals_repo.save_signal(
            Signal(
                signal_id=signal_id,
                symbol="MSFT",
                action=OrderAction.BUY,
                score=0.61,
                confidence=0.58,
                source="tradecopy",
                lane_hint="tradecopy",
                strategy_hint="copycat",
                status=SignalStatus.EXECUTED if index < 16 else SignalStatus.PENDING,
                created_at=created_at,
                metadata={"bot_id": "copycat"},
            )
        )
        pnl = -1.8 if index < 16 else 0.4
        outcome = TradeOutcomeStatus.WIN if pnl > 0 else TradeOutcomeStatus.LOSS
        await outcomes_repo.save_outcome(
            TradeOutcome(
                trade_id=f"copycat-trade-{index}",
                signal_id=signal_id,
                symbol="MSFT",
                outcome=outcome,
                pnl_pct=pnl,
                opened_at=created_at,
                closed_at=created_at + timedelta(hours=10),
                entry_price=100.0,
                exit_price=98.2 if pnl < 0 else 100.4,
                quantity=1.0,
                bot_id="copycat",
                source="tradecopy",
                features={
                    "bot_id": "copycat",
                    "source": "tradecopy",
                    "r_multiple": -0.9 if pnl < 0 else 0.2,
                    "sl_pct": 2.0,
                    "hold_hours": 10.0,
                    "regime": "BULL",
                },
            )
        )
