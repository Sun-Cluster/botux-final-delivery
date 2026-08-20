from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tortoise import Tortoise

from app.services.runtime.proof import RuntimeProofService
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
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


class _FakeScheduler:
    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": True,
            "active": True,
            "job_count": 2,
            "jobs": [
                {"name": "exits.tradecopy", "last_error": None},
                {"name": "exits.options", "last_error": ""},
            ],
        }


async def _run_runtime_proof_pack_case() -> None:
    await _setup_db()
    now = datetime.now(timezone.utc)
    await BotsRepository(connection=None).upsert_bot_profile(
        "copycat",
        {"enabled": True, "lifecycle_state": "paper"},
    )
    await SignalsRepository(connection=None).save_signal(
        Signal(
            signal_id="exit:tradecopy:AAPL:abc",
            symbol="AAPL",
            action=OrderAction.SELL,
            score=1.0,
            status=SignalStatus.PENDING,
            source="tradecopy",
            lane_hint="tradecopy",
            strategy_hint="position_exit",
            metadata={"exit_reason": "stop_loss"},
            created_at=now - timedelta(minutes=10),
        )
    )
    await SignalsRepository(connection=None).save_signal(
        Signal(
            signal_id="entry:tradecopy:AAPL:001",
            symbol="AAPL",
            action=OrderAction.BUY,
            score=0.9,
            status=SignalStatus.EXECUTED,
            source="tradecopy",
            lane_hint="tradecopy",
            created_at=now - timedelta(hours=3),
        )
    )
    await TradeOutcomesRepository(connection=None).save_outcome(
        TradeOutcome(
            trade_id="open-aapl-1",
            signal_id="entry:tradecopy:AAPL:001",
            symbol="AAPL",
            outcome=TradeOutcomeStatus.OPEN,
            pnl_pct=-1.5,
            opened_at=now - timedelta(hours=2),
            entry_price=100.0,
            quantity=1.0,
            bot_id="copycat",
            source="tradecopy",
        )
    )
    await AuditLogsRepository(connection=None).append(
        event_type="proof.auto_exit.action",
        actor="test",
        payload={
            "symbol": "AAPL",
            "lane": "tradecopy",
            "reason": "stop_loss",
            "qty": 1.0,
            "result": "submitted",
        },
    )

    container = SimpleNamespace(scheduler=_FakeScheduler())
    payload = await RuntimeProofService().build_runtime_pack(container=container, window_minutes=120)

    assert payload["schema_version"] == "runtime_proof.v1"
    assert payload["scheduler"]["active"] is True
    assert payload["scheduler"]["job_count"] == 2
    assert payload["exits"]["recent_exit_signals"] == 1
    assert "AAPL" in payload["exits"]["unresolved_symbols"]
    assert payload["exits"]["recent_auto_exit_events"] == 1
    assert payload["exits"]["recent_auto_exit_submitted"] == 1
    assert payload["bots"]["count"] >= 1

    proof_id = await RuntimeProofService().persist_runtime_pack(payload=payload)
    assert proof_id > 0

    await _teardown_db()


def test_runtime_proof_service_builds_and_persists_pack() -> None:
    asyncio.run(_run_runtime_proof_pack_case())
