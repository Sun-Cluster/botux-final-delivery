from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
from typing import Any, cast

from loguru import logger

from app.services.runtime_config.service import RuntimeConfigService
from domain.models.order_intent import OrderIntent
from runtime.logging import format_log_fields


pipeline_logger = logger.bind(pipeline_module=__name__)

@dataclass
class _IbkrQuote:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None


class IbkrAdapter:
    def __init__(self) -> None:
        self._host = "127.0.0.1"
        self._port = 4002
        self._client_id = 1
        self._account_id = ""
        self._timeout_seconds = 12.0
        self._real_enabled = False
        self._ib: object | None = None
        self._connected = False
        self._connect_lock = asyncio.Lock()
        # ib_insync expects all broker calls to run on a thread that has an event
        # loop attached. Keep every blocking IBKR call on one dedicated worker.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ibkr")
        self._reconnect_interval_seconds = 10.0
        self._next_reconnect_allowed_at = 0.0
        self._last_reconnect_skip_log_at = 0.0

    async def get_account(self) -> dict:
        await self._refresh_runtime_settings()
        if not await self._ensure_connected():
            return _disconnected_account_payload(
                reason="ibkr_not_connected" if self._real_enabled else "ibkr_not_configured",
                configured=self._real_enabled,
            )

        summary = await self._run_ib_call(self._get_account_sync)
        if summary is None:
            return _disconnected_account_payload(reason="ibkr_account_unavailable", configured=True)
        return summary

    async def get_positions(self) -> list[dict]:
        await self._refresh_runtime_settings()
        if not await self._ensure_connected():
            return []

        positions = await self._run_ib_call(self._get_positions_sync)
        if positions is None:
            return []
        return positions

    async def get_quote(self, symbol: str) -> dict:
        await self._refresh_runtime_settings()
        normalized = symbol.upper()
        if not await self._ensure_connected():
            return {
                "symbol": normalized,
                "bid": None,
                "ask": None,
                "last": None,
                "volume": None,
                "error": "ibkr_not_connected" if self._real_enabled else "ibkr_not_configured",
                "connected": False,
            }

        quote = await self._run_ib_call(self._get_quote_sync, normalized)
        if quote is None:
            return {
                "symbol": normalized,
                "bid": None,
                "ask": None,
                "last": None,
                "volume": None,
                "error": "ibkr_quote_unavailable",
                "connected": False,
            }
        return {
            "symbol": quote.symbol,
            "bid": quote.bid,
            "ask": quote.ask,
            "last": quote.last,
            "volume": quote.volume,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        await self._refresh_runtime_settings()
        if not await self._ensure_connected():
            pipeline_logger.log("WARNING", "pipeline.{} {}", "broker.submit_rejected", format_log_fields({"broker": "ibkr", "reason": "not_connected"}))
            return {
                "broker_order_id": None,
                "status": "rejected",
                "filled_qty": 0.0,
                "avg_price": None,
                "symbol": order_intent.symbol.upper(),
                "error": "ibkr_not_connected" if self._real_enabled else "ibkr_not_configured",
            }

        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.submit_requested", format_log_fields({"broker": "ibkr", "mode": "real", "symbol": order_intent.symbol, "action": order_intent.action.value, "quantity": order_intent.quantity, "order_type": order_intent.order_type}))
        payload = await self._run_ib_call(self._submit_order_sync, order_intent)
        if payload is None:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.submit_failed", format_log_fields({"broker": "ibkr", "mode": "real", "symbol": order_intent.symbol, "action": order_intent.action.value}))
            return {
                "broker_order_id": None,
                "status": "failed",
                "filled_qty": 0.0,
                "avg_price": None,
                "symbol": order_intent.symbol.upper(),
                "error": "ibkr_submit_failed",
            }
        return payload

    async def cancel_order(self, broker_order_id: str) -> dict:
        await self._refresh_runtime_settings()
        if not await self._ensure_connected():
            return {"broker_order_id": broker_order_id, "status": "rejected", "error": "ibkr_not_connected"}

        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.cancel_requested", format_log_fields({"broker": "ibkr", "mode": "real", "broker_order_id": broker_order_id}))
        canceled = await self._run_ib_call(self._cancel_order_sync, broker_order_id)
        if canceled is None:
            return {"broker_order_id": broker_order_id, "status": "error", "error": "ibkr_cancel_failed"}
        return canceled

    async def get_order_status(self, broker_order_id: str) -> dict:
        await self._refresh_runtime_settings()
        if not await self._ensure_connected():
            return {"broker_order_id": broker_order_id, "status": "rejected", "error": "ibkr_not_connected"}

        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.status_requested", format_log_fields({"broker": "ibkr", "mode": "real", "broker_order_id": broker_order_id}))
        status = await self._run_ib_call(self._get_order_status_sync, broker_order_id)
        if status is None:
            return {"broker_order_id": broker_order_id, "status": "error", "error": "ibkr_status_failed"}
        return status

    async def _refresh_runtime_settings(self) -> None:
        try:
            service = RuntimeConfigService()
            host = str((await service.resolve("broker.ibkr.host")).value or self._host).strip()
            port = int((await service.resolve("broker.ibkr.port")).value or self._port)
            client_id = int((await service.resolve("broker.ibkr.client_id")).value or self._client_id)
            account_id = str((await service.resolve("broker.ibkr.account_id")).value or "").strip()
            timeout_seconds = float((await service.resolve_float("broker.ibkr.timeout_seconds")).value)
            enabled = bool((await service.resolve_bool("broker.ibkr.real_enabled")).value)
        except Exception:
            return

        connection_changed = (
            host != self._host
            or port != self._port
            or client_id != self._client_id
            or account_id != self._account_id
        )
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account_id = account_id
        self._timeout_seconds = timeout_seconds
        self._real_enabled = enabled and find_spec("ib_insync") is not None
        if connection_changed:
            await self._disconnect_runtime_session()

    async def _ensure_connected(self) -> bool:
        if not self._real_enabled:
            pipeline_logger.log("INFO", "pipeline.{} {}", "broker.connect_skipped", format_log_fields({"broker": "ibkr", "reason": "real_disabled"}))
            return False
        if self._connected and self._is_ib_connected():
            return True
        now = time.monotonic()
        if now < self._next_reconnect_allowed_at:
            remaining = round(self._next_reconnect_allowed_at - now, 2)
            if now - self._last_reconnect_skip_log_at >= 5.0:
                self._last_reconnect_skip_log_at = now
                pipeline_logger.log("WARNING", "pipeline.{} {}", "broker.reconnect_throttled", format_log_fields({"broker": "ibkr", "retry_in_seconds": remaining, "interval_seconds": self._reconnect_interval_seconds}))
            return False
        async with self._connect_lock:
            if self._connected and self._is_ib_connected():
                return True
            connected = await self._run_ib_call(self._connect_sync)
            self._connected = connected
            if not connected:
                self._next_reconnect_allowed_at = time.monotonic() + self._reconnect_interval_seconds
            pipeline_logger.log("INFO" if connected else "WARNING", "pipeline.{} {}", "broker.connection_state", format_log_fields({"broker": "ibkr", "connected": connected, "host": self._host, "port": self._port, "client_id": self._client_id}))
            return connected

    async def _run_ib_call(self, func: Any, *args: object) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._call_with_thread_loop, func, *args)

    async def _disconnect_runtime_session(self) -> None:
        if self._ib is not None:
            try:
                await self._run_ib_call(self._disconnect_sync)
            except Exception:
                pass
        self._ib = None
        self._connected = False
        self._next_reconnect_allowed_at = 0.0

    def _call_with_thread_loop(self, func: Any, *args: object) -> Any:
        self._ensure_thread_event_loop()
        return func(*args)

    def _ensure_thread_event_loop(self) -> None:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    def _connect_sync(self) -> bool:
        if not self._real_enabled:
            return False
        try:
            from ib_insync import IB
        except ImportError as exc:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.connect_failed", format_log_fields({"broker": "ibkr", "reason": "ib_insync_missing", "error": str(exc)}))
            return False

        if self._ib is None:
            self._ib = IB()
        ib = cast(Any, self._ib)
        if hasattr(ib, "isConnected") and ib.isConnected():
            return True
        try:
            ib.connect(host=self._host, port=self._port, clientId=self._client_id, timeout=self._timeout_seconds)
            ib.reqMarketDataType(3)
            if not self._account_id:
                accounts = ib.managedAccounts()
                if accounts:
                    self._account_id = str(accounts[0])
            return bool(ib.isConnected())
        except Exception as exc:
            self._connected = False
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.connect_failed", format_log_fields({"broker": "ibkr", "host": self._host, "port": self._port, "client_id": self._client_id, "timeout_seconds": self._timeout_seconds, "error": repr(exc)}))
            return False

    def _is_ib_connected(self) -> bool:
        ib = cast(Any, self._ib)
        if ib is None or not hasattr(ib, "isConnected"):
            return False
        try:
            return bool(ib.isConnected())
        except Exception:
            return False

    def _disconnect_sync(self) -> None:
        ib = cast(Any, self._ib)
        if ib is None:
            return
        try:
            if hasattr(ib, "isConnected") and ib.isConnected():
                ib.disconnect()
        except Exception:
            return

    def _get_account_sync(self) -> dict | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            summary = ib.accountSummary(self._account_id)
        except Exception:
            return None
        equity = 0.0
        cash = 0.0
        buying_power = 0.0
        currency = "AUD"
        for item in summary:
            tag = str(getattr(item, "tag", ""))
            value = getattr(item, "value", None)
            if tag == "NetLiquidation":
                equity = _to_float(value)
            elif tag == "AvailableFunds":
                buying_power = _to_float(value)
            elif tag == "TotalCashValue":
                cash = _to_float(value)
            elif tag == "Currency":
                currency = str(value or currency)
        return {
            "equity": equity,
            "cash": cash,
            "buying_power": buying_power,
            "currency": currency,
            "mode": "paper" if self._port in {4002, 7497} else "live",
            "broker": "ibkr",
            "account": self._account_id,
            "connected": True,
            "configured": True,
        }

    def _get_positions_sync(self) -> list[dict] | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            positions = ib.positions(self._account_id)
        except Exception:
            return None
        rows: list[dict] = []
        for position in positions:
            contract = getattr(position, "contract", None)
            qty = _to_float(getattr(position, "position", 0.0))
            avg_cost = _to_float(getattr(position, "avgCost", 0.0))
            symbol = str(getattr(contract, "symbol", ""))
            exchange = str(getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", ""))
            full_symbol = f"{symbol}.AX" if exchange.upper() == "ASX" and not symbol.endswith(".AX") else symbol
            quote = self._get_quote_sync(full_symbol)
            current_price = 0.0 if quote is None or quote.last is None else float(quote.last)
            market_value = abs(qty) * current_price if current_price > 0 else abs(qty) * avg_cost
            unrealized_pl = market_value - (abs(qty) * avg_cost)
            unrealized_plpc = (unrealized_pl / (abs(qty) * avg_cost)) if avg_cost > 0 and qty != 0 else 0.0
            rows.append(
                {
                    "symbol": full_symbol,
                    "quantity": qty,
                    "qty": qty,
                    "avg_entry_price": avg_cost,
                    "current_price": current_price,
                    "market_value": market_value,
                    "unrealized_pl": unrealized_pl,
                    "unrealized_plpc": unrealized_plpc,
                    "broker": "ibkr",
                    "currency": "AUD" if full_symbol.endswith(".AX") else "USD",
                    "side": "long" if qty >= 0 else "short",
                }
            )
        return rows

    def _get_quote_sync(self, symbol: str) -> _IbkrQuote | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            contract = _resolve_contract(symbol)
            ib.qualifyContracts(contract)
            ticker = ib.reqMktData(contract, "", False, False)
            ib.sleep(1.5)
            ib.cancelMktData(contract)
        except Exception:
            return None
        bid = _to_optional_float(getattr(ticker, "bid", None))
        ask = _to_optional_float(getattr(ticker, "ask", None))
        last = _to_optional_float(getattr(ticker, "last", None))
        volume = _to_optional_int(getattr(ticker, "volume", None))
        return _IbkrQuote(symbol=symbol, bid=bid, ask=ask, last=last, volume=volume)

    def _submit_order_sync(self, order_intent: OrderIntent) -> dict | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            from ib_insync import LimitOrder, MarketOrder
        except ImportError:
            return None
        try:
            contract = _resolve_contract(order_intent.symbol.upper())
            ib.qualifyContracts(contract)
            limit_price = _resolve_limit_price(order_intent)
            if order_intent.order_type.lower() == "limit" and limit_price is not None:
                order = LimitOrder(order_intent.action.value.upper(), int(order_intent.quantity), limit_price)
            else:
                order = MarketOrder(order_intent.action.value.upper(), int(order_intent.quantity))
            trade = ib.placeOrder(contract, order)
            for _ in range(5):
                ib.sleep(0.5)
                status = str(getattr(getattr(trade, "orderStatus", None), "status", "")).lower()
                if status in {"submitted", "presubmitted", "filled", "cancelled"}:
                    break
            order_status = getattr(trade, "orderStatus", None)
            filled_qty = _to_float(getattr(order_status, "filled", 0.0))
            avg_price = _to_optional_float(getattr(order_status, "avgFillPrice", None))
            return {
                "broker_order_id": str(getattr(getattr(trade, "order", None), "orderId", "")),
                "status": str(getattr(order_status, "status", "submitted")).lower(),
                "filled_qty": filled_qty,
                "avg_price": avg_price,
                "symbol": order_intent.symbol.upper(),
            }
        except Exception:
            return None

    def _cancel_order_sync(self, broker_order_id: str) -> dict | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            target_id = int(broker_order_id)
        except ValueError:
            return {"broker_order_id": broker_order_id, "status": "not_found"}
        try:
            for trade in ib.openTrades():
                order = getattr(trade, "order", None)
                if int(getattr(order, "orderId", -1)) != target_id:
                    continue
                ib.cancelOrder(order)
                ib.sleep(0.2)
                return {"broker_order_id": broker_order_id, "status": "canceled"}
        except Exception:
            return None
        return {"broker_order_id": broker_order_id, "status": "not_found"}

    def _get_order_status_sync(self, broker_order_id: str) -> dict | None:
        ib = cast(Any, self._ib)
        if ib is None:
            return None
        try:
            target_id = int(broker_order_id)
        except ValueError:
            return {"broker_order_id": broker_order_id, "status": "not_found"}
        try:
            for trade in ib.openTrades():
                order = getattr(trade, "order", None)
                if int(getattr(order, "orderId", -1)) != target_id:
                    continue
                order_status = getattr(trade, "orderStatus", None)
                return {
                    "broker_order_id": broker_order_id,
                    "status": str(getattr(order_status, "status", "unknown")).lower(),
                    "filled_qty": _to_float(getattr(order_status, "filled", 0.0)),
                    "avg_price": _to_optional_float(getattr(order_status, "avgFillPrice", None)),
                    "symbol": _contract_symbol(getattr(trade, "contract", None)),
                }
            for fill in ib.fills():
                execution = getattr(fill, "execution", None)
                if int(getattr(execution, "orderId", -1)) != target_id:
                    continue
                contract = getattr(fill, "contract", None)
                return {
                    "broker_order_id": broker_order_id,
                    "status": "filled",
                    "filled_qty": _to_float(getattr(execution, "shares", 0.0)),
                    "avg_price": _to_optional_float(getattr(execution, "avgPrice", None)),
                    "symbol": _contract_symbol(contract),
                }
        except Exception:
            return None
        return {"broker_order_id": broker_order_id, "status": "not_found"}


def _resolve_contract(symbol: str) -> object:
    from ib_insync import Stock

    normalized = symbol.upper()
    if normalized.endswith(".AX"):
        bare = normalized.removesuffix(".AX")
        return Stock(bare, "SMART", "AUD", primaryExchange="ASX")
    return Stock(normalized, "SMART", "USD")


def _contract_symbol(contract: object | None) -> str:
    if contract is None:
        return ""
    symbol = str(getattr(contract, "symbol", ""))
    exchange = str(getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", ""))
    if exchange.upper() == "ASX" and not symbol.endswith(".AX"):
        return f"{symbol}.AX"
    return symbol

def _to_float(value: object) -> float:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _to_optional_float(value: object) -> float | None:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _to_optional_int(value: object) -> int | None:
    try:
        return int(float(cast(float | int | str, value)))
    except (TypeError, ValueError):
        return None


def _disconnected_account_payload(*, reason: str, configured: bool) -> dict[str, object]:
    return {
        "equity": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "currency": "USD",
        "mode": "paper",
        "broker": "ibkr",
        "account": "",
        "connected": False,
        "configured": configured,
        "status": "disconnected",
        "error": reason,
    }


def _resolve_limit_price(order_intent: OrderIntent) -> float | None:
    metadata = order_intent.metadata
    for key in ("limit_price", "reference_price", "entry_price", "last_price"):
        value = metadata.get(key)
        price = _to_optional_float(value)
        if price is not None and price > 0:
            return price
    return None
