from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import TYPE_CHECKING

from app.services.lanes.options import OptionsLaneService, _exit_watch as options_exit_watch
from app.services.signals.service import SignalService
from app.services.lanes.evo_catalyst import (
    EQUITY_STOP_LOSS_PCT as EVO_EQUITY_STOP_LOSS_PCT,
    EQUITY_TAKE_PROFIT_PCT as EVO_EQUITY_TAKE_PROFIT_PCT,
    ETF_STOP_LOSS_PCT as EVO_ETF_STOP_LOSS_PCT,
    ETF_TAKE_PROFIT_PCT as EVO_ETF_TAKE_PROFIT_PCT,
)
from app.services.lanes.swingtrade import SwingtradeLaneService, _exit_watch as swing_exit_watch
from app.services.lanes.tradecopy import (
    MAX_HOLD_DAYS as TRADECOPY_MAX_HOLD_DAYS,
    PROFIT_TARGET_PCT as TRADECOPY_PROFIT_TARGET_PCT,
    STOP_LOSS_PCT as TRADECOPY_STOP_LOSS_PCT,
    TradecopyLaneService,
)
from app.usecases.submit_order import submit_order
from db.repositories._common import JSONValue
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.orders_repo import OrdersRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import OrderAction, SignalStatus
from domain.models.signal import Signal

if TYPE_CHECKING:
    from runtime.container import Container

EXIT_SUBMIT_GUARD_TTL_SECONDS = 120
_OPTION_CONTRACT_PATTERN = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")
EVO_MAX_HOLD_DAYS = 20


class LaneExitService:
    def __init__(self, *, exit_guard_ttl_seconds: int = EXIT_SUBMIT_GUARD_TTL_SECONDS) -> None:
        self._exit_guard_ttl_seconds = max(1, int(exit_guard_ttl_seconds))
        self._exit_guard_until: dict[str, datetime] = {}

    async def run_tradecopy_exits(self, *, container: "Container") -> dict[str, object]:
        service = TradecopyLaneService()
        outcomes = await service._tradecopy_outcomes(limit=1000)
        position_map = await _broker_position_map(container)
        submitted = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        for row in outcomes:
            if str(row.get("outcome")) != "open":
                continue
            symbol = str(row.get("symbol", "")).upper().strip()
            if not symbol:
                skipped += 1
                continue
            days_held = _days_held(row.get("opened_at"))
            pnl_pct = _to_float(row.get("pnl_pct"))
            reason = _tradecopy_exit_reason(days_held=days_held, pnl_pct=pnl_pct)
            if reason is None:
                skipped += 1
                continue
            skip_reason = await self._skip_exit_submit_reason(symbol=symbol, lane="tradecopy")
            if skip_reason is not None:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="tradecopy",
                    reason=reason,
                    quantity=0.0,
                    result=f"skipped:{skip_reason}",
                )
                continue
            qty = _position_qty(position_map.get(symbol))
            if qty <= 0:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="tradecopy",
                    reason=reason,
                    quantity=qty,
                    result="skipped:no_position_qty",
                )
                continue
            ok, error = await _submit_close_signal(
                container=container,
                symbol=symbol,
                quantity=qty,
                lane="tradecopy",
                reason=reason,
            )
            if ok:
                submitted += 1
                self._touch_exit_submit_guard(symbol=symbol, lane="tradecopy")
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="tradecopy",
                    reason=reason,
                    quantity=qty,
                    result="submitted",
                )
            else:
                errors.append({"symbol": symbol, "reason": reason, "error": error})
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="tradecopy",
                    reason=reason,
                    quantity=qty,
                    result="error",
                    error=error,
                )
        return {"checked": len(outcomes), "submitted": submitted, "skipped": skipped, "errors": errors}

    async def run_options_exits(self, *, container: "Container") -> dict[str, object]:
        service = OptionsLaneService()
        rows = await service._options_outcomes(limit=1000)
        position_map = await _broker_position_map(container)
        submitted = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        for row in rows:
            if row.status != "open":
                continue
            snapshot = await service._position_snapshot(row)
            exit_item = options_exit_watch(snapshot)
            if exit_item is None:
                skipped += 1
                continue
            symbol = str(snapshot.get("contract") or row.symbol).upper().strip()
            underlying = str(snapshot.get("underlying") or row.underlying or "").upper().strip()
            reason = str(exit_item.get("reason") or "exit_rule")
            skip_reason = await self._skip_exit_submit_reason(symbol=symbol, lane="options")
            if skip_reason is not None:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="options",
                    reason=reason,
                    quantity=0.0,
                    result=f"skipped:{skip_reason}",
                )
                continue
            qty = _position_qty(position_map.get(symbol))
            if qty <= 0:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="options",
                    reason=reason,
                    quantity=qty,
                    result="skipped:no_position_qty",
                )
                continue
            ok, error = await _submit_close_signal(
                container=container,
                symbol=symbol,
                quantity=qty,
                lane="options",
                reason=reason,
                market="options_us",
                extra_metadata={"underlying_symbol": underlying, "option_contract": symbol},
            )
            if ok:
                submitted += 1
                self._touch_exit_submit_guard(symbol=symbol, lane="options")
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="options",
                    reason=reason,
                    quantity=qty,
                    result="submitted",
                )
            else:
                errors.append({"symbol": symbol, "reason": reason, "error": error})
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="options",
                    reason=reason,
                    quantity=qty,
                    result="error",
                    error=error,
                )
        return {"checked": len(rows), "submitted": submitted, "skipped": skipped, "errors": errors}

    async def run_swingtrade_exits(self, *, container: "Container") -> dict[str, object]:
        service = SwingtradeLaneService()
        rows = await service._swing_outcomes(limit=1000)
        position_map = await _broker_position_map(container)
        submitted = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        for row in rows:
            if row.status != "open":
                continue
            snapshot = await service._position_snapshot(row)
            exit_item = swing_exit_watch(snapshot)
            if exit_item is None:
                skipped += 1
                continue
            symbol = row.symbol.upper().strip()
            reason = str(exit_item.get("reason") or "exit_rule")
            skip_reason = await self._skip_exit_submit_reason(symbol=symbol, lane="swingtrade")
            if skip_reason is not None:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="swingtrade",
                    reason=reason,
                    quantity=0.0,
                    result=f"skipped:{skip_reason}",
                )
                continue
            qty = _position_qty(position_map.get(symbol))
            if qty <= 0:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="swingtrade",
                    reason=reason,
                    quantity=qty,
                    result="skipped:no_position_qty",
                )
                continue
            ok, error = await _submit_close_signal(
                container=container,
                symbol=symbol,
                quantity=qty,
                lane="swingtrade",
                reason=reason,
            )
            if ok:
                submitted += 1
                self._touch_exit_submit_guard(symbol=symbol, lane="swingtrade")
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="swingtrade",
                    reason=reason,
                    quantity=qty,
                    result="submitted",
                )
            else:
                errors.append({"symbol": symbol, "reason": reason, "error": error})
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="swingtrade",
                    reason=reason,
                    quantity=qty,
                    result="error",
                    error=error,
                )
        return {"checked": len(rows), "submitted": submitted, "skipped": skipped, "errors": errors}

    async def run_evo_catalyst_exits(self, *, container: "Container") -> dict[str, object]:
        rows = await _evo_outcomes(limit=1000)
        position_map = await _broker_position_map(container)
        submitted = 0
        skipped = 0
        errors: list[dict[str, object]] = []
        for row in rows:
            if str(row.get("outcome")) != "open":
                continue
            symbol = str(row.get("symbol", "")).upper().strip()
            if not symbol:
                skipped += 1
                continue
            days_held = _days_held(row.get("opened_at"))
            pnl_pct = _to_float(row.get("pnl_pct"))
            market = str(row.get("market", "")).lower()
            reason = _evo_exit_reason(symbol=symbol, market=market, days_held=days_held, pnl_pct=pnl_pct)
            if reason is None:
                skipped += 1
                continue
            skip_reason = await self._skip_exit_submit_reason(symbol=symbol, lane="evo_catalyst")
            if skip_reason is not None:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="evo_catalyst",
                    reason=reason,
                    quantity=0.0,
                    result=f"skipped:{skip_reason}",
                )
                continue
            qty = _position_qty(position_map.get(symbol))
            if qty <= 0:
                skipped += 1
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="evo_catalyst",
                    reason=reason,
                    quantity=qty,
                    result="skipped:no_position_qty",
                )
                continue
            ok, error = await _submit_close_signal(
                container=container,
                symbol=symbol,
                quantity=qty,
                lane="evo_catalyst",
                reason=reason,
            )
            if ok:
                submitted += 1
                self._touch_exit_submit_guard(symbol=symbol, lane="evo_catalyst")
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="evo_catalyst",
                    reason=reason,
                    quantity=qty,
                    result="submitted",
                )
            else:
                errors.append({"symbol": symbol, "reason": reason, "error": error})
                await _append_auto_exit_evidence(
                    symbol=symbol,
                    lane="evo_catalyst",
                    reason=reason,
                    quantity=qty,
                    result="error",
                    error=error,
                )
        return {"checked": len(rows), "submitted": submitted, "skipped": skipped, "errors": errors}

    async def _skip_exit_submit_reason(self, *, symbol: str, lane: str) -> str | None:
        if self._has_recent_exit_submit(symbol=symbol, lane=lane):
            return "guard_ttl"
        if await _has_active_exit_order(symbol=symbol, lane=lane):
            return "active_exit_order"
        return None

    def _has_recent_exit_submit(self, *, symbol: str, lane: str) -> bool:
        now = datetime.now(timezone.utc)
        key = _exit_guard_key(symbol=symbol, lane=lane)
        stale = [item for item, expires_at in self._exit_guard_until.items() if expires_at <= now]
        for item in stale:
            self._exit_guard_until.pop(item, None)
        expires_at = self._exit_guard_until.get(key)
        return bool(expires_at is not None and expires_at > now)

    def _touch_exit_submit_guard(self, *, symbol: str, lane: str) -> None:
        key = _exit_guard_key(symbol=symbol, lane=lane)
        self._exit_guard_until[key] = datetime.now(timezone.utc) + timedelta(
            seconds=self._exit_guard_ttl_seconds
        )


async def _broker_position_map(container: "Container") -> dict[str, dict[str, object]]:
    try:
        positions = await container.broker.get_positions()
    except Exception:
        return {}
    result: dict[str, dict[str, object]] = {}
    for row in positions:
        if not isinstance(row, dict):
            continue
        aliases = _position_aliases(row)
        if not aliases:
            continue
        for alias in aliases:
            _merge_position_for_alias(result=result, alias=alias, row=row)
    return result


async def _submit_close_signal(
    *,
    container: "Container",
    symbol: str,
    quantity: float,
    lane: str,
    reason: str,
    market: str | None = None,
    extra_metadata: dict[str, JSONValue] | None = None,
) -> tuple[bool, str | None]:
    payload: dict[str, JSONValue] = {
        "exit_reason": reason,
        "exit_lane": lane,
        "market": market or ("asx_equities" if symbol.upper().endswith(".AX") else "us_equities"),
    }
    if extra_metadata:
        payload.update(extra_metadata)
    close_signal = Signal(
        signal_id=f"exit:{lane}:{symbol}:{uuid4().hex[:12]}",
        symbol=symbol,
        action=OrderAction.SELL,
        score=1.0,
        confidence=1.0,
        source=lane,
        lane_hint=lane,
        strategy_hint="position_exit",
        headline=f"Auto exit {lane} {symbol} ({reason})"[:200],
        status=SignalStatus.PENDING,
        metadata=payload,
        created_at=datetime.now(timezone.utc),
    )
    try:
        await SignalService().ingest_signal(close_signal)
        execution = await submit_order(close_signal, quantity=quantity, broker_router=container.broker_router)
    except Exception as exc:
        return False, str(exc)[:200]
    if execution is None:
        return False, "execution_none"
    if execution.status.value in {"failed", "rejected", "canceled", "expired"}:
        return False, execution.status.value
    return True, None


def _tradecopy_exit_reason(*, days_held: int, pnl_pct: float) -> str | None:
    if pnl_pct <= TRADECOPY_STOP_LOSS_PCT:
        return "stop_loss"
    if pnl_pct >= TRADECOPY_PROFIT_TARGET_PCT:
        return "take_profit"
    if days_held >= TRADECOPY_MAX_HOLD_DAYS:
        return "max_hold"
    return None


def _evo_exit_reason(*, symbol: str, market: str, days_held: int, pnl_pct: float) -> str | None:
    is_us_etf = market == "us_equities" or symbol == "LIT"
    stop_loss_pct = EVO_ETF_STOP_LOSS_PCT if is_us_etf else EVO_EQUITY_STOP_LOSS_PCT
    take_profit_pct = EVO_ETF_TAKE_PROFIT_PCT if is_us_etf else EVO_EQUITY_TAKE_PROFIT_PCT
    if pnl_pct <= -abs(stop_loss_pct):
        return "stop_loss"
    if pnl_pct >= abs(take_profit_pct):
        return "take_profit"
    if days_held >= EVO_MAX_HOLD_DAYS:
        return "max_hold"
    return None


def _days_held(opened_at: object) -> int:
    if isinstance(opened_at, datetime):
        return max(0, (datetime.now(timezone.utc) - opened_at).days)
    if isinstance(opened_at, str):
        try:
            dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        except ValueError:
            return 0
        return max(0, (datetime.now(timezone.utc) - dt).days)
    return 0


def _position_qty(position: dict[str, object] | None) -> float:
    if not isinstance(position, dict):
        return 0.0
    return max(
        0.0,
        _to_float(position.get("qty"))
        or _to_float(position.get("quantity"))
        or _to_float(position.get("available_qty")),
    )


def _to_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _position_aliases(position: dict[str, object]) -> set[str]:
    aliases: set[str] = set()
    direct_symbol = _normalized_symbol(position.get("symbol"))
    underlying_symbol = _normalized_symbol(position.get("underlying_symbol") or position.get("underlying"))
    currency = _normalized_symbol(position.get("currency"))

    for symbol in (direct_symbol, underlying_symbol):
        if not symbol:
            continue
        aliases.add(symbol)
        if symbol.endswith(".AX"):
            aliases.add(symbol.removesuffix(".AX"))
        elif currency == "AUD" and symbol.isalpha() and 2 <= len(symbol) <= 5:
            aliases.add(f"{symbol}.AX")

    if direct_symbol:
        option_underlying = _option_underlying_from_contract(direct_symbol)
        if option_underlying:
            aliases.add(option_underlying)
    return aliases


def _option_underlying_from_contract(symbol: str) -> str | None:
    compact = symbol.replace(" ", "")
    match = _OPTION_CONTRACT_PATTERN.match(compact)
    if match is None:
        return None
    return match.group(1)


def _merge_position_for_alias(
    *,
    result: dict[str, dict[str, object]],
    alias: str,
    row: dict[str, object],
) -> None:
    current = result.get(alias)
    if current is None:
        copied = dict(row)
        copied["symbol"] = alias
        result[alias] = copied
        return
    merged_qty = _position_qty(current) + _position_qty(row)
    current["qty"] = merged_qty
    current["quantity"] = merged_qty
    current["symbol"] = alias


def _normalized_symbol(value: object) -> str:
    return str(value or "").upper().strip()


async def _has_active_exit_order(*, symbol: str, lane: str) -> bool:
    try:
        async with UnitOfWork() as uow:
            repo = OrdersRepository(connection=uow.connection)
            return await repo.has_active_exit_for_symbol(symbol=symbol, lane=lane)
    except Exception:
        return False


async def _evo_outcomes(*, limit: int) -> list[dict[str, object]]:
    try:
        async with UnitOfWork() as uow:
            rows = await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
    except Exception:
        return []
    payloads: list[dict[str, object]] = []
    for row in rows:
        if str(row.bot_id or "") != "evo_catalyst" and str(row.source or "") != "evo_catalyst":
            continue
        payloads.append(
            {
                "symbol": row.symbol,
                "outcome": row.outcome.value,
                "pnl_pct": row.pnl_pct,
                "opened_at": row.opened_at.isoformat(),
                "market": row.market,
            }
        )
    return payloads


async def _append_auto_exit_evidence(
    *,
    symbol: str,
    lane: str,
    reason: str,
    quantity: float,
    result: str,
    error: str | None = None,
) -> None:
    payload: dict[str, JSONValue] = {
        "symbol": symbol.upper().strip(),
        "lane": lane.strip().lower(),
        "reason": reason.strip().lower() if reason.strip() else "exit_rule",
        "qty": float(quantity),
        "result": result,
        "error": error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            await repo.append(
                event_type="proof.auto_exit.action",
                actor="lane_exit_service",
                payload=_json_payload(payload),
            )
    except Exception:
        return


def _json_payload(raw: dict[str, JSONValue]) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {}
    for key, value in raw.items():
        if isinstance(key, str) and _is_json_value(value):
            payload[key] = value
    return payload


def _is_json_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False


def _exit_guard_key(*, symbol: str, lane: str) -> str:
    return f"{lane.strip().lower()}:{symbol.upper().strip()}"
