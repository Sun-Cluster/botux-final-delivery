from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, cast

from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from infra.brokers.base import BrokerPort


class OutcomeLifecycleService:
    def __init__(self, broker: BrokerPort | None = None) -> None:
        self._broker = broker

    async def reconcile_open_outcomes(self) -> dict[str, object]:
        async with UnitOfWork() as uow:
            repo = TradeOutcomesRepository(connection=uow.connection)
            open_rows = await repo.list_open_rows(limit=1000)

        broker_positions = await self._broker.get_positions() if self._broker is not None else []
        broker_symbols = {
            _normalize_symbol(position.get("symbol"))
            for position in broker_positions
            if _position_qty(position) > 0
        }
        broker_symbols.discard("")

        closed: list[dict[str, object]] = []
        orphan_open: list[str] = []
        quotes_checked = 0
        fills_checked = 0
        quote_cache: dict[str, float] = {}
        fills_cache: dict[str, float] = {}
        for row in open_rows:
            symbol = _normalize_symbol(row.symbol)
            if symbol in broker_symbols:
                continue
            exit_price, used_fill, used_quote = await _resolve_exit_price(
                symbol=symbol,
                row=row,
                broker=self._broker,
                fills_cache=fills_cache,
                quote_cache=quote_cache,
            )
            if used_fill:
                fills_checked += 1
            if used_quote:
                quotes_checked += 1
            if exit_price <= 0:
                orphan_open.append(symbol)
                continue
            async with UnitOfWork() as uow:
                repo = TradeOutcomesRepository(connection=uow.connection)
                closed_row = await repo.close_open_outcome(
                    symbol=symbol,
                    exit_price=exit_price,
                    reason="broker_position_absent",
                    closed_at=datetime.now(timezone.utc),
                )
            if closed_row is not None:
                closed.append(
                    {
                        "symbol": closed_row.symbol,
                        "outcome": closed_row.outcome,
                        "pnl_pct": closed_row.pnl_pct,
                    }
                )

        return {
            "checked": len(open_rows),
            "broker_positions": sorted(broker_symbols),
            "closed": closed,
            "closed_count": len(closed),
            "orphan_open": sorted(orphan_open),
            "fills_checked": fills_checked,
            "quotes_checked": quotes_checked,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def _normalize_symbol(value: object) -> str:
    symbol = str(value or "").upper().strip()
    if symbol.endswith(".AX"):
        return symbol[:-3]
    return symbol


async def _resolve_exit_price(
    *,
    symbol: str,
    row: object,
    broker: BrokerPort | None,
    fills_cache: dict[str, float],
    quote_cache: dict[str, float],
) -> tuple[float, bool, bool]:
    from_fills, used_fill = await _exit_price_from_recent_sell_fill(
        symbol=symbol,
        broker=broker,
        fills_cache=fills_cache,
    )
    if from_fills > 0:
        return from_fills, used_fill, False

    row_exit = _to_float(getattr(row, "exit_price", None))
    if row_exit > 0:
        return row_exit, False, False

    if broker is None or not symbol:
        return 0.0, False, False
    cached = quote_cache.get(symbol)
    if cached is not None and cached > 0:
        return cached, False, True
    try:
        quote = await broker.get_quote(symbol)
    except Exception:
        quote = {}
    price = _to_float(
        quote.get("last")
        or quote.get("current_price")
        or quote.get("price")
        or quote.get("ask")
        or quote.get("bid")
    )
    if price > 0:
        quote_cache[symbol] = price
        return price, False, True

    return 0.0, False, True


async def _exit_price_from_recent_sell_fill(
    *,
    symbol: str,
    broker: BrokerPort | None,
    fills_cache: dict[str, float],
) -> tuple[float, bool]:
    if broker is None or not symbol:
        return 0.0, False
    cached = fills_cache.get(symbol)
    if cached is not None and cached > 0:
        return cached, True
    get_recent_fills = getattr(broker, "get_recent_fills", None)
    if get_recent_fills is None:
        return 0.0, False
    try:
        result = get_recent_fills(symbol=symbol, limit=10)
        fills = await result if inspect.isawaitable(result) else result
    except Exception:
        return 0.0, True
    if not isinstance(fills, list):
        return 0.0, True
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        side = str(fill.get("side") or "").lower()
        if side != "sell":
            continue
        price = _to_float(fill.get("filled_avg_price") or fill.get("avg_price") or fill.get("price"))
        if price > 0:
            fills_cache[symbol] = price
            return price, True
    return 0.0, True


def _to_float(value: object) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return 0.0


def _position_qty(position: object) -> float:
    if not isinstance(position, dict):
        return 0.0
    return max(
        0.0,
        _to_float(position.get("quantity"))
        or _to_float(position.get("qty")),
    )
