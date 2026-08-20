from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from api.routers.core_api import (
    _apply_point_in_time_excursion_fallback,
    _backfill_trade_excursions,
    _dedupe_trade_rows,
    _trade_payload,
)


def test_trade_payload_does_not_fallback_truth_fields_from_features() -> None:
    opened_at = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)
    closed_at = datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc)
    item = SimpleNamespace(
        trade_id="reference-1",
        signal_id="tradecopy:reference",
        symbol="AAPL",
        action=None,
        quantity=None,
        pnl_pct=None,
        outcome="win",
        bot_id=None,
        source=None,
        entry_price=None,
        exit_price=None,
        close_reason=None,
        opened_at=opened_at,
        closed_at=closed_at,
        features={
            "qty": 3.0,
            "action": "buy",
            "source": "tradecopy",
            "pnl_pct": 1.25,
            "mfe_pct": 3.5,
            "mae_pct": -1.2,
            "entry_price": 100.0,
            "exit_price": 101.25,
            "close_reason": "take_profit",
        },
    )

    payload = _trade_payload(item)

    assert payload["action"] is None
    assert payload["quantity"] is None
    assert payload["qty"] is None
    assert payload["bot_id"] is None
    assert payload["source"] is None
    assert payload["pnl_pct"] is None
    assert payload["entry_price"] is None
    assert payload["exit_price"] is None
    assert payload["close_reason"] is None
    assert payload["mfe_pct"] == 3.5
    assert payload["mae_pct"] == -1.2
    assert payload["opened_at"] == opened_at.isoformat()
    assert payload["created_at"] == opened_at.isoformat()
    assert payload["closed_at"] == closed_at.isoformat()


def test_trade_payload_requires_canonical_excursion_keys() -> None:
    opened_at = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)
    item = SimpleNamespace(
        trade_id="reference-2",
        signal_id="tradecopy:reference2",
        symbol="MSFT",
        action="buy",
        quantity=1.0,
        pnl_pct=None,
        outcome="open",
        bot_id=None,
        source="tradecopy",
        entry_price=100.0,
        exit_price=None,
        close_reason=None,
        opened_at=opened_at,
        closed_at=None,
        features={
            "bot_pnl_pct": -0.85,
            "max_favorable_excursion_pct": 2.0,
            "max_adverse_excursion_pct": -1.5,
        },
    )

    payload = _trade_payload(item)

    assert payload["pnl_pct"] is None
    assert payload["mfe_pct"] is None
    assert payload["mae_pct"] is None


def test_trade_payload_uses_db_fields_when_available() -> None:
    opened_at = datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc)
    item = SimpleNamespace(
        trade_id="db-row-1",
        signal_id="sig-db-row-1",
        symbol="NVDA",
        action="buy",
        quantity=2.0,
        pnl_pct=2.0,
        outcome="open",
        bot_id="turbo",
        source="turbo",
        entry_price=100.0,
        exit_price=None,
        close_reason=None,
        opened_at=opened_at,
        closed_at=None,
        features={},
    )
    payload = _trade_payload(item)
    assert payload["action"] == "BUY"
    assert payload["quantity"] == 2.0
    assert payload["pnl_pct"] == 2.0
    assert payload["bot_id"] == "turbo"
    assert payload["source"] == "turbo"
    assert payload["entry_price"] == 100.0


def test_dedupe_trade_rows_prefers_canonical_non_sync_bot_rows() -> None:
    rows = [
        {
            "trade_id": "1672",
            "signal_id": "broker_sync:alpaca:a0782dd4-fcfd-44e4-b9b3-9f1f2d7e0211",
            "symbol": "GOOGL",
            "source": "alpaca_sync",
            "bot_id": "unknown",
            "broker_order_id": "a0782dd4-fcfd-44e4-b9b3-9f1f2d7e0211",
            "created_at": "2026-06-09T00:43:03.805554+00:00",
            "features": {"signal_id": "broker_sync:alpaca:a0782dd4-fcfd-44e4-b9b3-9f1f2d7e0211"},
        },
        {
            "trade_id": "1619",
            "signal_id": "news_alpaca_news_GOOGL_921fb9789e24",
            "symbol": "GOOGL",
            "source": "alpaca_news",
            "bot_id": "turbo",
            "broker_order_id": "a0782dd4-fcfd-44e4-b9b3-9f1f2d7e0211",
            "created_at": "2026-06-09T00:43:03.163781+00:00",
            "features": {"signal_id": "news_alpaca_news_GOOGL_921fb9789e24"},
        },
    ]

    deduped = _dedupe_trade_rows(rows)

    assert len(deduped) == 1
    assert deduped[0]["bot_id"] == "turbo"
    assert deduped[0]["source"] == "alpaca_news"


def test_backfill_trade_excursions_uses_position_snapshots() -> None:
    rows = [
        {
            "symbol": "AAPL",
            "action": "BUY",
            "entry_price": 100.0,
            "exit_price": None,
            "pnl_pct": None,
            "mfe_pct": None,
            "mae_pct": None,
            "broker_name": "alpaca",
            "opened_at": "2026-06-10T10:00:00+00:00",
            "created_at": "2026-06-10T10:00:00+00:00",
            "closed_at": None,
        }
    ]
    snapshots = [
        {
            "created_at": "2026-06-10T10:01:00+00:00",
            "payload": {
                "positions": [
                    {"symbol": "AAPL", "broker": "alpaca", "current_price": 103.0},
                ]
            },
        },
        {
            "created_at": "2026-06-10T10:02:00+00:00",
            "payload": {
                "positions": [
                    {"symbol": "AAPL", "broker": "alpaca", "current_price": 98.0},
                ]
            },
        },
    ]

    hydrated = _backfill_trade_excursions(rows=rows, snapshots=snapshots)

    assert hydrated[0]["mfe_pct"] == 3.0
    assert hydrated[0]["mae_pct"] == -2.0


def test_point_in_time_excursion_fallback_uses_exit_price() -> None:
    row = {
        "symbol": "NVDA",
        "action": "BUY",
        "entry_price": 100.0,
        "exit_price": 96.0,
        "pnl_pct": -4.0,
        "mfe_pct": None,
        "mae_pct": None,
    }

    hydrated = _apply_point_in_time_excursion_fallback(row)

    assert hydrated["mfe_pct"] == 0.0
    assert hydrated["mae_pct"] == -4.0
