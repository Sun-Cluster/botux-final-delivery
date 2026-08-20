from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from db.repositories.orders_repo import OrdersRepository
from db.repositories.executions_repo import ExecutionsRepository
from db.repositories.positions_repo import PositionSnapshotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import ExecutionStatus
from infra.brokers.base import BrokerPort


class ReconcileService:
    def __init__(self, broker: BrokerPort | None = None) -> None:
        self._broker = broker

    async def run(self) -> dict:
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        account = await self._load_account()
        positions = await self._load_positions()

        report: dict[str, object] = {
            "timestamp": now.isoformat(),
            "account": {
                "equity": _as_float(account.get("equity")),
                "cash": _as_float(account.get("cash")),
                "buying_power": _as_float(account.get("buying_power")),
                "portfolio_value": _as_float(account.get("portfolio_value", account.get("equity"))),
                "day_pnl": _as_float(account.get("equity")) - _as_float(account.get("last_equity")),
            },
            "positions": [],
            "daily_pnl": 0.0,
            "winners": [],
            "losers": [],
            "issues": [],
            "signals_pending": 0,
            "signals_stale": 0,
            "trades_today": 0,
            "open_symbols_db": [],
            "open_symbols_broker": [],
            "stale_signals_details": [],
            "open_outcomes": [],
            "execution_summary": {},
            "reconciliation": {},
            "db_truth": {},
            "source": "broker" if self._broker is not None else "no_broker",
        }
        account_payload = report["account"]
        if isinstance(account_payload, dict):
            report["daily_pnl"] = _as_float(account_payload.get("day_pnl"))

        winners: list[dict[str, object]] = []
        losers: list[dict[str, object]] = []
        normalized_positions: list[dict[str, object]] = []
        broker_symbols: set[str] = set()
        for raw in positions:
            symbol = str(raw.get("symbol", "")).upper()
            if not symbol:
                continue
            quantity = _as_float(raw.get("quantity", raw.get("qty")))
            pnl = _as_float(raw.get("unrealized_pl"))
            pnl_pct = _as_float(raw.get("unrealized_plpc", raw.get("unrealized_pnl_pct")))
            market_value = _as_float(raw.get("market_value"))
            entry_price = _as_float(raw.get("avg_entry", raw.get("avg_entry_price", raw.get("avg_price"))))
            current_price = _as_float(raw.get("current_price", raw.get("last_price")))
            if market_value == 0.0 and current_price > 0:
                market_value = abs(quantity) * current_price

            position_info: dict[str, object] = {
                "symbol": symbol,
                "qty": quantity,
                "entry_price": round(entry_price, 4),
                "current_price": round(current_price, 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 4),
                "market_value": round(market_value, 4),
            }
            normalized_positions.append(position_info)
            broker_symbols.add(symbol)
            if pnl >= 0:
                winners.append(position_info)
            else:
                losers.append(position_info)

        winners.sort(key=lambda row: _as_float(row.get("pnl")), reverse=True)
        losers.sort(key=lambda row: _as_float(row.get("pnl")))

        report["positions"] = normalized_positions
        report["winners"] = winners
        report["losers"] = losers
        report["open_symbols_broker"] = sorted(broker_symbols)

        async with UnitOfWork() as uow:
            signals_repo = SignalsRepository(connection=uow.connection)
            orders_repo = OrdersRepository(connection=uow.connection)
            executions_repo = ExecutionsRepository(connection=uow.connection)
            outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
            snapshots_repo = PositionSnapshotsRepository(connection=uow.connection)

            pending_count = await signals_repo.count_pending()
            stale_count = await signals_repo.count_pending_older_than(now - timedelta(hours=24))
            stale_signals = await signals_repo.list_pending_older_than(now - timedelta(hours=24), limit=50)
            orders_today = await orders_repo.count_since(start_of_day)
            trades_today = await executions_repo.count_since(
                start_of_day,
                statuses={ExecutionStatus.FILLED, ExecutionStatus.EXECUTED},
            )
            execution_rows = await executions_repo.list_since(start_of_day, limit=200)
            open_rows = await outcomes_repo.list_open_rows(limit=1000)
            open_symbols_db = await outcomes_repo.list_open_symbols()
            open_outcomes_count = await outcomes_repo.count_open()
            closed_outcomes_today = await outcomes_repo.count_closed_since(start_of_day)
            recent_snapshots = await snapshots_repo.list_recent(limit=3)

        report["signals_pending"] = pending_count
        report["signals_stale"] = stale_count
        report["trades_today"] = trades_today
        report["open_symbols_db"] = sorted(open_symbols_db)
        report["stale_signals_details"] = [
            {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "source": signal.source,
                "lane_hint": signal.lane_hint,
                "strategy_hint": signal.strategy_hint,
                "blocked_reason": signal.blocked_reason,
                "age_hours": round(max((now - signal.created_at).total_seconds(), 0.0) / 3600.0, 3),
                "created_at": signal.created_at.isoformat(),
            }
            for signal in stale_signals
        ]
        report["open_outcomes"] = [_open_outcome_payload(row) for row in open_rows]

        missing_in_broker = sorted(open_symbols_db - broker_symbols)
        missing_in_db = sorted(broker_symbols - open_symbols_db)
        open_trade_ids = {
            str(item["trade_id"])
            for item in report["open_outcomes"]
            if isinstance(item, dict) and isinstance(item.get("trade_id"), str)
        }
        filled_without_open_outcome = []
        execution_rows_payload: list[dict[str, object]] = []
        for row in execution_rows:
            order = row.order
            signal = None if order is None else order.signal
            payload = {
                "execution_id": int(row.id),
                "order_id": str(getattr(row, "order_id", "")),
                "signal_id": None if signal is None else signal.signal_id,
                "symbol": "" if order is None else order.symbol,
                "status": row.status,
                "broker_order_id": row.broker_order_id,
                "broker_name": None if order is None else order.broker_name,
                "market": None if order is None else order.market,
                "order_type": None if order is None else order.order_type,
                "filled_qty": float(row.filled_qty),
                "avg_price": None if row.avg_price is None else float(row.avg_price),
                "created_at": row.created_at.isoformat(),
            }
            execution_rows_payload.append(payload)
            if row.status in {ExecutionStatus.FILLED.value, ExecutionStatus.EXECUTED.value} and payload["order_id"] not in open_trade_ids:
                filled_without_open_outcome.append(payload)

        latest_snapshot_at = None
        if recent_snapshots:
            raw_created_at = recent_snapshots[0].get("created_at")
            if isinstance(raw_created_at, str):
                latest_snapshot_at = raw_created_at
        report["execution_summary"] = {
            "orders_today": orders_today,
            "executions_today": len(execution_rows_payload),
            "filled_executions_today": trades_today,
            "filled_without_open_outcome": filled_without_open_outcome,
            "recent_executions": execution_rows_payload[:20],
        }
        report["reconciliation"] = {
            "missing_in_broker": missing_in_broker,
            "missing_in_db": missing_in_db,
            "filled_without_open_outcome": [str(item["order_id"]) for item in filled_without_open_outcome],
            "position_snapshot_count": len(recent_snapshots),
            "latest_snapshot_at": latest_snapshot_at,
        }
        report["db_truth"] = {
            "pending_signals": pending_count,
            "stale_signals_24h": stale_count,
            "orders_today": orders_today,
            "executions_today": len(execution_rows_payload),
            "filled_executions_today": trades_today,
            "open_outcomes": open_outcomes_count,
            "closed_outcomes_today": closed_outcomes_today,
            "position_snapshots": len(recent_snapshots),
            "latest_snapshot_at": latest_snapshot_at,
        }
        issues: list[str] = []
        if stale_count > 10:
            issues.append(f"{stale_count} stale signals (>24h pending)")
        if not normalized_positions and trades_today == 0:
            issues.append("No positions and no trades today - verify system is active")
        if missing_in_broker:
            issues.append(
                f"Open outcomes missing at broker: {', '.join(missing_in_broker)}"
            )
        if missing_in_db:
            issues.append(
                f"Broker positions missing open outcomes: {', '.join(missing_in_db)}"
            )
        if filled_without_open_outcome:
            missing_ids = ", ".join(str(item["order_id"]) for item in filled_without_open_outcome[:10])
            issues.append(f"Filled executions missing open outcome truth: {missing_ids}")
        report["issues"] = issues
        return report

    async def _load_account(self) -> dict[str, object]:
        if self._broker is None:
            return {}
        payload = await self._broker.get_account()
        return payload if isinstance(payload, dict) else {}

    async def _load_positions(self) -> list[dict[str, object]]:
        if self._broker is None:
            return []
        payload = await self._broker.get_positions()
        rows: list[dict[str, object]] = []
        for row in payload:
            if isinstance(row, dict):
                rows.append(row)
        return rows


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _open_outcome_payload(row: object) -> dict[str, object]:
    order = getattr(row, "order", None)
    signal = getattr(row, "signal", None)
    trade_id = getattr(row, "trade_id", None)
    return {
        "trade_id": str(trade_id or getattr(row, "order_id", "") or getattr(row, "id", "")),
        "signal_id": None if signal is None else signal.signal_id,
        "symbol": getattr(row, "symbol", ""),
        "source": getattr(row, "source", None),
        "bot_id": getattr(row, "bot_id", None),
        "broker_name": None if order is None else order.broker_name,
        "market": None if order is None else order.market,
        "order_type": None if order is None else order.order_type,
        "entry_price": getattr(row, "entry_price", None),
        "quantity": getattr(row, "quantity", None),
        "opened_at": getattr(row, "created_at").isoformat(),
    }
