from __future__ import annotations

import asyncio

from api.routers.api_extra import _account_from_portfolio_payload
from app.services.portfolio.service import PortfolioService


class _FakeBroker:
    async def get_account(self) -> dict[str, object]:
        return {
            "equity": 1200.0,
            "last_equity": 1000.0,
            "cash": 300.0,
            "buying_power": 900.0,
            "mode": "paper",
            "currency": "USD",
        }

    async def get_positions(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": "AAPL",
                "qty": 2,
                "avg_entry_price": 100.0,
                "current_price": 110.0,
                "market_value": 220.0,
                "unrealized_pl": 20.0,
                "unrealized_plpc": 0.1,
            }
        ]


def test_account_from_portfolio_payload_maps_snapshot_fields() -> None:
    payload = {
        "equity": 1200.0,
        "last_equity": 1000.0,
        "cash": 300.0,
        "buying_power": 900.0,
        "mode": "paper",
        "currency": "USD",
        "daytrade_count": 1,
    }
    account = _account_from_portfolio_payload(payload, container_broker_mode="paper")
    assert account["equity"] == 1200.0
    assert account["last_equity"] == 1000.0
    assert account["cash"] == 300.0
    assert account["buying_power"] == 900.0
    assert account["mode"] == "paper"
    assert account["currency"] == "USD"
    assert account["daytrade_count"] == 1


def test_portfolio_service_payload_includes_account_and_excursion_fields() -> None:
    payload = asyncio.run(PortfolioService(broker=_FakeBroker())._build_payload())
    assert payload["equity"] == 1200.0
    assert payload["last_equity"] == 1000.0
    assert payload["buying_power"] == 900.0
    assert payload["daily_pnl"] == 200.0
    assert payload["daily_pnl_pct"] == 20.0
    positions = payload["positions"]
    assert isinstance(positions, list) and positions
    first = positions[0]
    assert first["qty"] == 2.0
    assert first["unrealized_pl"] == 20.0
    assert first["unrealized_plpc"] == 0.1
