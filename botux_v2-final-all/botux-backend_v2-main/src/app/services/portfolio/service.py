from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from db.repositories.positions_repo import PositionSnapshotsRepository
from db.uow import UnitOfWork
from infra.brokers.base import BrokerPort


class PortfolioService:
    def __init__(self, broker: BrokerPort | None = None) -> None:
        self._broker = broker

    async def snapshot(self) -> dict:
        payload = await self._build_payload()
        snapshot_key = f"portfolio:{uuid4().hex}"
        async with UnitOfWork() as uow:
            repo = PositionSnapshotsRepository(connection=uow.connection)
            await repo.save_snapshot(snapshot_key, payload)
        return payload

    async def allocation_summary(self) -> dict[str, object]:
        payload = await self._build_payload()
        deployed_pct = _as_float(payload.get("deployed_pct"), 0.0)
        return {
            "equity": _as_float(payload.get("equity"), 0.0),
            "deployed_pct": round(deployed_pct * 100, 2),
            "cash_pct": round((1.0 - deployed_pct) * 100, 2) if deployed_pct <= 1.0 else 0.0,
            "position_count": _as_int(payload.get("position_count"), 0),
            "positions": payload.get("positions", []),
            "snapshot_at": payload.get("snapshot_at"),
            "source": payload.get("source"),
        }

    async def _build_payload(self) -> dict[str, object]:
        if self._broker is None:
            return {
                "snapshot_at": datetime.now(timezone.utc).isoformat(),
                "equity": 0.0,
                "cash": 0.0,
                "positions": [],
                "position_count": 0,
                "deployed_pct": 0.0,
                "source": "no_broker",
            }

        account = await self._broker.get_account()
        positions = await self._broker.get_positions()
        equity = _as_float(account.get("equity"), 0.0)
        last_equity = _as_float(account.get("last_equity"), 0.0)
        cash = _as_float(account.get("cash"), 0.0)
        buying_power = _as_float(account.get("buying_power"), cash)
        daily_pnl = equity - last_equity
        daily_pnl_pct = ((daily_pnl / last_equity) * 100.0) if last_equity > 0 else 0.0
        total_market_value = 0.0
        normalized_positions: list[dict[str, object]] = []
        for position in positions:
            symbol = str(position.get("symbol", "")).upper()
            qty = _as_float(position.get("quantity", position.get("qty", 0.0)), 0.0)
            avg_entry_price = _as_float(
                position.get("avg_entry_price", position.get("avg_entry")),
                0.0,
            )
            market_value = _as_float(position.get("market_value"), 0.0)
            current_price = _as_float(position.get("current_price"), 0.0)
            unrealized_pl = _as_float(position.get("unrealized_pl"), 0.0)
            unrealized_plpc = _as_float(
                position.get("unrealized_plpc", position.get("unrealized_pnl_pct")),
                0.0,
            )
            if market_value == 0.0:
                market_value = abs(qty) * current_price
            total_market_value += abs(market_value)
            normalized_positions.append(
                {
                    "symbol": symbol,
                    "quantity": qty,
                    "qty": qty,
                    "side": "long" if qty >= 0 else "short",
                    "avg_entry_price": round(avg_entry_price, 4),
                    "market_value": round(market_value, 2),
                    "current_price": round(current_price, 4),
                    "unrealized_pl": round(unrealized_pl, 4),
                    "unrealized_plpc": round(unrealized_plpc, 6),
                    "broker": str(position.get("broker", "paper")),
                    "currency": str(position.get("currency", account.get("currency", "USD"))),
                }
            )

        deployed_pct = (total_market_value / equity) if equity > 0 else 0.0
        return {
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "equity": round(equity, 2),
            "last_equity": round(last_equity, 2),
            "cash": round(cash, 2),
            "buying_power": round(buying_power, 2),
            "daily_pnl": round(daily_pnl, 4),
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "mode": str(account.get("mode", "paper")),
            "currency": str(account.get("currency", "USD")),
            "positions": normalized_positions,
            "position_count": len(normalized_positions),
            "deployed_pct": round(deployed_pct, 4),
            "source": "broker",
        }


def _as_float(value: object, default: float) -> float:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(cast(float | int | str, value))
    except (TypeError, ValueError):
        return default
