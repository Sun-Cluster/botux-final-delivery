from __future__ import annotations

from datetime import datetime, timezone
from loguru import logger
import math
import re
import time
from typing import cast

import httpx

from app.services.runtime_config.service import RuntimeConfigService
from domain.models.order_intent import OrderIntent
from runtime.logging import format_log_fields

pipeline_logger = logger.bind(pipeline_module=__name__)

JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
_OPTION_CONTRACT_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


class AlpacaAdapter:
    def __init__(self) -> None:
        self._api_key = ""
        self._secret_key = ""
        self._base_url = "https://paper-api.alpaca.markets"
        self._data_url = "https://data.alpaca.markets"
        self._timeout_seconds = 15.0
        self._real_enabled = False
        self._disconnect_log_interval_seconds = 60.0
        self._last_connection_state: bool | None = None
        self._last_disconnected_log_at = 0.0

    async def get_account(self) -> dict:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            self._log_connection_state(
                level="ERROR",
                connected=False,
                configured=False,
                reason="alpaca_credentials_missing_or_disabled",
            )
            return _disabled_account_payload(
                broker="alpaca",
                reason="alpaca_credentials_missing_or_disabled",
                configured=False,
            )

        raw = await self._request("GET", "/v2/account", base_url=self._base_url)
        if raw is None:
            pipeline_logger.log("INFO", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": "get_account"}))
            self._log_connection_state(
                level="WARNING",
                connected=False,
                configured=True,
                reason="alpaca_account_unavailable",
            )
            return _disabled_account_payload(
                broker="alpaca",
                reason="alpaca_account_unavailable",
                configured=True,
            )

        self._log_connection_state(
            level="INFO",
            connected=True,
            configured=True,
            mode="paper" if "paper" in self._base_url.lower() else "live",
        )
        return {
            "equity": _to_float(raw.get("equity")),
            "cash": _to_float(raw.get("cash")),
            "buying_power": _to_float(raw.get("buying_power")),
            "last_equity": _to_float(raw.get("last_equity")),
            "portfolio_value": _to_float(raw.get("portfolio_value")),
            "currency": str(raw.get("currency", "USD")),
            "mode": "paper" if "paper" in self._base_url.lower() else "live",
            "broker": "alpaca",
            "status": str(raw.get("status", "unknown")),
            "account_number": str(raw.get("account_number", "")),
            "connected": True,
            "configured": True,
        }

    def _log_connection_state(self, *, level: str, connected: bool, configured: bool, **extra: object) -> None:
        should_log = False
        if connected:
            should_log = self._last_connection_state is not True
        else:
            now = time.monotonic()
            if self._last_connection_state is not False:
                should_log = True
            elif now - self._last_disconnected_log_at >= self._disconnect_log_interval_seconds:
                should_log = True
            if should_log:
                self._last_disconnected_log_at = now

        self._last_connection_state = connected
        if not should_log:
            return

        pipeline_logger.log(level, "pipeline.{} {}", "broker.connection_state", format_log_fields({"broker": "alpaca", "connected": connected, "configured": configured}))

    async def get_positions(self) -> list[dict]:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            return []

        raw = await self._request_list("GET", "/v2/positions", base_url=self._base_url)
        if raw is None:
            return []

        rows: list[dict] = []
        for item in raw:
            rows.append(
                {
                    "symbol": str(item.get("symbol", "")),
                    "quantity": _to_float(item.get("qty")),
                    "qty": _to_float(item.get("qty")),
                    "avg_entry_price": _to_float(item.get("avg_entry_price")),
                    "current_price": _to_float(item.get("current_price")),
                    "market_value": _to_float(item.get("market_value")),
                    "unrealized_pl": _to_float(item.get("unrealized_pl")),
                    "unrealized_plpc": _to_float(item.get("unrealized_plpc")),
                    "broker": "alpaca",
                    "currency": "USD",
                    "side": str(item.get("side", "long")),
                }
            )
        return rows

    async def get_quote(self, symbol: str) -> dict:
        await self._refresh_runtime_settings()
        normalized = symbol.upper()
        if not self._real_enabled:
            return {
                "symbol": normalized,
                "bid": None,
                "ask": None,
                "last": None,
                "error": "alpaca_not_configured",
                "connected": False,
            }

        if _is_option_contract_symbol(normalized):
            snapshot_raw = await self._request(
                "GET",
                "/v1beta1/options/snapshots",
                base_url=self._data_url,
                params={"symbols": normalized, "feed": "indicative"},
            )
            snapshots = cast(
                dict[str, JSONValue] | None,
                snapshot_raw.get("snapshots") if snapshot_raw is not None else None,
            )
            snapshot = cast(dict[str, JSONValue] | None, snapshots.get(normalized) if snapshots is not None else None)
            quote = cast(dict[str, JSONValue] | None, snapshot.get("latestQuote") if snapshot is not None else None)
            trade = cast(dict[str, JSONValue] | None, snapshot.get("latestTrade") if snapshot is not None else None)
            if quote is None and trade is None:
                return {
                    "symbol": normalized,
                    "bid": None,
                    "ask": None,
                    "last": None,
                    "error": "alpaca_quote_unavailable",
                    "connected": False,
                }
            return {
                "symbol": normalized,
                "bid": None if quote is None else _to_float(quote.get("bp")),
                "ask": None if quote is None else _to_float(quote.get("ap")),
                "last": None if trade is None else _to_float(trade.get("p")),
                "timestamp": None if trade is None and quote is None else str((trade or quote or {}).get("t", "")),
            }

        quote_raw = await self._request(
            "GET",
            f"/v2/stocks/{normalized}/quotes/latest",
            base_url=self._data_url,
            params={"feed": "iex"},
        )
        trade_raw = await self._request(
            "GET",
            f"/v2/stocks/{normalized}/trades/latest",
            base_url=self._data_url,
            params={"feed": "iex"},
        )
        quote = cast(dict[str, JSONValue] | None, quote_raw.get("quote") if quote_raw is not None else None)
        trade = cast(dict[str, JSONValue] | None, trade_raw.get("trade") if trade_raw is not None else None)

        if quote is None and trade is None:
            return {
                "symbol": normalized,
                "bid": None,
                "ask": None,
                "last": None,
                "error": "alpaca_quote_unavailable",
                "connected": False,
            }

        return {
            "symbol": normalized,
            "bid": None if quote is None else _to_float(quote.get("bp")),
            "ask": None if quote is None else _to_float(quote.get("ap")),
            "last": None if trade is None else _to_float(trade.get("p")),
            "timestamp": (
                None
                if trade is None and quote is None
                else str((trade or quote or {}).get("t", ""))
            ),
        }

    async def submit_order(self, order_intent: OrderIntent) -> dict:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            pipeline_logger.log("WARNING", "pipeline.{} {}", "broker.submit_rejected", format_log_fields({"broker": "alpaca", "reason": "not_configured"}))
            return {
                "broker_order_id": None,
                "status": "rejected",
                "filled_qty": 0.0,
                "avg_price": None,
                "symbol": order_intent.symbol.upper(),
                "error": "alpaca_not_configured",
            }

        payload = _build_alpaca_order_payload(order_intent)
        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.submit_requested", format_log_fields({"broker": "alpaca", "mode": "real", "symbol": order_intent.symbol, "action": order_intent.action.value, "quantity": order_intent.quantity, "order_type": order_intent.order_type, "payload": payload}))
        raw = await self._request("POST", "/v2/orders", base_url=self._base_url, json_data=payload)
        if raw is None:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.submit_failed", format_log_fields({"broker": "alpaca", "mode": "real", "symbol": order_intent.symbol, "action": order_intent.action.value}))
            return {
                "broker_order_id": None,
                "status": "failed",
                "filled_qty": 0.0,
                "avg_price": None,
                "symbol": order_intent.symbol.upper(),
                "error": "alpaca_submit_failed",
            }
        return {
            "broker_order_id": str(raw.get("id", "")),
            "status": str(raw.get("status", "submitted")).lower(),
            "filled_qty": _to_float(raw.get("filled_qty")),
            "avg_price": _to_optional_float(raw.get("filled_avg_price")),
            "symbol": str(raw.get("symbol", order_intent.symbol.upper())),
        }

    async def get_option_chain(
        self,
        symbol: str,
        *,
        expiration_date_gte: str | None = None,
        expiration_date_lte: str | None = None,
        option_type: str = "call",
        limit: int = 50,
    ) -> list[dict[str, JSONValue]]:
        await self._refresh_runtime_settings()
        normalized = symbol.upper().strip()
        if not self._real_enabled or not normalized:
            return []

        params: dict[str, str] = {
            "underlying_symbols": normalized,
            "status": "active",
            "limit": str(max(1, min(limit, 100))),
            "type": option_type.strip().lower(),
        }
        if expiration_date_gte:
            params["expiration_date_gte"] = expiration_date_gte
        if expiration_date_lte:
            params["expiration_date_lte"] = expiration_date_lte

        contracts_raw = await self._request(
            "GET",
            "/v2/options/contracts",
            base_url=self._base_url,
            params=params,
        )
        contract_rows = cast(
            list[dict[str, JSONValue]] | None,
            contracts_raw.get("option_contracts") if contracts_raw is not None else None,
        )
        if not contract_rows:
            return []

        tradable = [row for row in contract_rows if bool(row.get("tradable"))]
        if not tradable:
            return []

        symbols = [str(row.get("symbol", "")).upper() for row in tradable if str(row.get("symbol", "")).strip()]
        if not symbols:
            return []

        snapshots_raw = await self._request(
            "GET",
            "/v1beta1/options/snapshots",
            base_url=self._data_url,
            params={"symbols": ",".join(symbols), "feed": "indicative"},
        )
        raw_snapshots = snapshots_raw.get("snapshots") if snapshots_raw is not None else None
        snapshots = cast(dict[str, dict[str, JSONValue]], raw_snapshots if isinstance(raw_snapshots, dict) else {})
        spot_quote = await self.get_quote(normalized)
        spot = _to_float(spot_quote.get("last")) or _to_float(spot_quote.get("ask")) or _to_float(spot_quote.get("bid"))
        today = datetime.now(timezone.utc).date()

        results: list[dict[str, JSONValue]] = []
        for contract in tradable:
            contract_symbol = str(contract.get("symbol", "")).upper()
            if not contract_symbol:
                continue
            snapshot = snapshots.get(contract_symbol, {})
            quote = cast(dict[str, JSONValue], snapshot.get("latestQuote") or {})
            trade = cast(dict[str, JSONValue], snapshot.get("latestTrade") or {})
            strike = _to_float(contract.get("strike_price"))
            expiration = str(contract.get("expiration_date", ""))
            expiration_date = expiration[:10]
            try:
                dte = (datetime.strptime(expiration_date, "%Y-%m-%d").date() - today).days
            except ValueError:
                dte = 0
            est_delta = _estimate_delta(
                strike=strike,
                spot=spot,
                dte=dte,
                option_type=str(contract.get("type", option_type)).lower(),
            )
            results.append(
                {
                    "symbol": contract_symbol,
                    "underlying": str(contract.get("underlying_symbol", normalized)).upper(),
                    "strike_price": strike,
                    "expiration_date": expiration,
                    "type": str(contract.get("type", option_type)).lower(),
                    "bid_price": _to_float(quote.get("bp")),
                    "ask_price": _to_float(quote.get("ap")),
                    "last_price": _to_float(trade.get("p")),
                    "greeks": {
                        "delta": round(est_delta, 4),
                        "gamma": 0.0,
                        "theta": 0.0,
                        "vega": 0.0,
                        "implied_volatility": 0.0,
                    },
                }
            )
        return results

    async def cancel_order(self, broker_order_id: str) -> dict:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            return {"broker_order_id": broker_order_id, "status": "rejected", "error": "alpaca_not_configured"}

        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.cancel_requested", format_log_fields({"broker": "alpaca", "mode": "real", "broker_order_id": broker_order_id}))
        payload = await self._request(
            "DELETE",
            f"/v2/orders/{broker_order_id}",
            base_url=self._base_url,
            accept_statuses={200, 204, 404, 422},
        )
        if payload is None:
            return {"broker_order_id": broker_order_id, "status": "error", "error": "alpaca_cancel_failed"}
        if payload.get("status_code") == 404:
            return {"broker_order_id": broker_order_id, "status": "not_found"}
        return {"broker_order_id": broker_order_id, "status": "canceled"}

    async def get_order_status(self, broker_order_id: str) -> dict:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            return {"broker_order_id": broker_order_id, "status": "rejected", "error": "alpaca_not_configured"}

        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.status_requested", format_log_fields({"broker": "alpaca", "mode": "real", "broker_order_id": broker_order_id}))
        raw = await self._request(
            "GET",
            f"/v2/orders/{broker_order_id}",
            base_url=self._base_url,
            params={"nested": "true"},
            accept_statuses={200, 404},
        )
        if raw is None:
            return {"broker_order_id": broker_order_id, "status": "error", "error": "alpaca_status_failed"}
        if raw.get("status_code") == 404:
            return {"broker_order_id": broker_order_id, "status": "not_found"}
        return {
            "broker_order_id": str(raw.get("id", broker_order_id)),
            "status": str(raw.get("status", "unknown")).lower(),
            "filled_qty": _to_float(raw.get("filled_qty")),
            "avg_price": _to_optional_float(raw.get("filled_avg_price")),
            "symbol": str(raw.get("symbol", "")),
        }

    async def get_recent_fills(self, *, symbol: str, limit: int = 10) -> list[dict[str, JSONValue]]:
        await self._refresh_runtime_settings()
        normalized = symbol.upper().strip()
        if not self._real_enabled or not normalized:
            return []
        safe_limit = max(1, min(limit, 50))
        raw = await self._request_list(
            "GET",
            "/v2/orders",
            base_url=self._base_url,
            params={
                "status": "closed",
                "symbols": normalized,
                "direction": "desc",
                "limit": str(safe_limit),
                "nested": "false",
            },
        )
        if raw is None:
            return []
        rows: list[dict[str, JSONValue]] = []
        for item in raw:
            rows.append(
                {
                    "id": str(item.get("id", "")),
                    "symbol": str(item.get("symbol", normalized)).upper(),
                    "side": str(item.get("side", "")).lower(),
                    "status": str(item.get("status", "")).lower(),
                    "filled_qty": _to_float(item.get("filled_qty")),
                    "filled_avg_price": _to_optional_float(item.get("filled_avg_price")),
                    "filled_at": str(item.get("filled_at", "")),
                }
            )
        return rows

    async def list_orders(
        self,
        *,
        status: str = "all",
        limit: int = 100,
    ) -> list[dict[str, JSONValue]]:
        await self._refresh_runtime_settings()
        if not self._real_enabled:
            return []
        safe_limit = max(1, min(limit, 500))
        normalized_status = status.strip().lower() if status else "all"
        if normalized_status not in {"open", "closed", "all"}:
            normalized_status = "all"
        raw = await self._request_list(
            "GET",
            "/v2/orders",
            base_url=self._base_url,
            params={
                "status": normalized_status,
                "direction": "desc",
                "limit": str(safe_limit),
                "nested": "false",
            },
        )
        if raw is None:
            return []
        rows: list[dict[str, JSONValue]] = []
        for item in raw:
            rows.append(
                {
                    "id": str(item.get("id", "")),
                    "symbol": str(item.get("symbol", "")).upper(),
                    "side": str(item.get("side", "")).lower(),
                    "status": str(item.get("status", "")).lower(),
                    "type": str(item.get("type", "market")).lower(),
                    "qty": _to_float(item.get("qty")),
                    "filled_qty": _to_float(item.get("filled_qty")),
                    "filled_avg_price": _to_optional_float(item.get("filled_avg_price")),
                    "submitted_at": str(item.get("submitted_at", "")),
                    "filled_at": str(item.get("filled_at", "")),
                    "updated_at": str(item.get("updated_at", "")),
                    "created_at": str(item.get("created_at", "")),
                }
            )
        return rows

    async def _refresh_runtime_settings(self) -> None:
        try:
            service = RuntimeConfigService()
            self._api_key = str((await service.resolve("broker.alpaca.api_key")).value or "").strip()
            self._secret_key = str((await service.resolve("broker.alpaca.secret_key")).value or "").strip()
            self._base_url = str((await service.resolve("broker.alpaca.base_url")).value or self._base_url).strip()
            self._data_url = str((await service.resolve("broker.alpaca.data_url")).value or self._data_url).strip()
            self._timeout_seconds = float((await service.resolve_float("broker.alpaca.timeout_seconds")).value)
            enabled = bool((await service.resolve_bool("broker.alpaca.real_enabled")).value)
            self._real_enabled = enabled and bool(self._api_key and self._secret_key)
        except Exception:
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str,
        params: dict[str, str] | None = None,
        json_data: dict[str, object] | None = None,
        accept_statuses: set[int] | None = None,
    ) -> dict[str, JSONValue] | None:
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Content-Type": "application/json",
        }
        allowed = accept_statuses or {200, 201, 204}
        async with httpx.AsyncClient(headers=headers, timeout=self._timeout_seconds) as client:
            try:
                response = await client.request(method, f"{base_url}{path}", params=params, json=json_data)
            except httpx.HTTPError as exc:
                pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "error": repr(exc)}))
                return None
        if response.status_code not in allowed:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "status_code": response.status_code, "body": response.text[:300]}))
            return None
        if response.status_code == 204:
            return {"status_code": 204}
        try:
            payload = response.json()
        except ValueError as exc:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "status_code": response.status_code, "error": f"invalid_json:{exc}"}))
            return None
        if not isinstance(payload, dict):
            return None
        normalized = dict(payload)
        normalized["status_code"] = response.status_code
        return cast(dict[str, JSONValue], normalized)

    async def _request_list(
        self,
        method: str,
        path: str,
        *,
        base_url: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, JSONValue]] | None:
        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=self._timeout_seconds) as client:
            try:
                response = await client.request(method, f"{base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "error": repr(exc)}))
                return None
        if response.status_code not in {200, 201}:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "status_code": response.status_code, "body": response.text[:300]}))
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.request_failed", format_log_fields({"broker": "alpaca", "operation": f"{method} {path}", "base_url": base_url, "status_code": response.status_code, "error": f"invalid_json:{exc}"}))
            return None
        if not isinstance(payload, list):
            return None
        rows: list[dict[str, JSONValue]] = []
        for item in payload:
            if isinstance(item, dict):
                rows.append(cast(dict[str, JSONValue], item))
        return rows

def _build_alpaca_order_payload(order_intent: OrderIntent) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": order_intent.symbol.upper(),
        "qty": str(order_intent.quantity),
        "side": order_intent.action.value,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": order_intent.idempotency_key,
    }
    order_type = order_intent.order_type.lower()
    if order_type == "limit":
        limit_price = _resolve_limit_price(order_intent)
        if limit_price is not None:
            payload["type"] = "limit"
            payload["limit_price"] = str(limit_price)
        return payload
    if order_type == "bracket":
        take_profit = _resolve_take_profit_price(order_intent)
        stop_loss = _resolve_stop_loss_price(order_intent)
        if take_profit is not None and stop_loss is not None:
            payload["order_class"] = "bracket"
            payload["take_profit"] = {"limit_price": str(take_profit)}
            payload["stop_loss"] = {"stop_price": str(stop_loss)}
            limit_price = _resolve_limit_price(order_intent)
            if limit_price is not None:
                payload["type"] = "limit"
                payload["limit_price"] = str(limit_price)
        return payload
    return payload


def _resolve_limit_price(order_intent: OrderIntent) -> float | None:
    metadata = order_intent.metadata
    for key in ("limit_price", "reference_price", "entry_price", "last_price"):
        value = metadata.get(key)
        price = _to_optional_float(value)
        if price is not None and price > 0:
            return price
    return None


def _resolve_take_profit_price(order_intent: OrderIntent) -> float | None:
    metadata = order_intent.metadata
    explicit = _to_optional_float(metadata.get("take_profit_price"))
    if explicit is not None and explicit > 0:
        return explicit
    reference = _resolve_limit_price(order_intent)
    take_profit_pct = _to_optional_float(metadata.get("take_profit_pct"))
    if reference is None or take_profit_pct is None:
        return None
    multiplier = 1.0 + take_profit_pct if order_intent.action.value == "buy" else 1.0 - take_profit_pct
    return round(reference * multiplier, 4)


def _resolve_stop_loss_price(order_intent: OrderIntent) -> float | None:
    metadata = order_intent.metadata
    explicit = _to_optional_float(metadata.get("stop_loss_price"))
    if explicit is not None and explicit > 0:
        return explicit
    reference = _resolve_limit_price(order_intent)
    stop_loss_pct = _to_optional_float(metadata.get("stop_loss_pct"))
    if reference is None or stop_loss_pct is None:
        return None
    multiplier = 1.0 - stop_loss_pct if order_intent.action.value == "buy" else 1.0 + stop_loss_pct
    return round(reference * multiplier, 4)


def _disabled_account_payload(*, broker: str, reason: str, configured: bool) -> dict[str, object]:
    return {
        "equity": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "last_equity": 0.0,
        "portfolio_value": 0.0,
        "currency": "USD",
        "mode": "paper",
        "broker": broker,
        "status": "disconnected",
        "account_number": "",
        "connected": False,
        "configured": configured,
        "error": reason,
    }


def _to_float(value: JSONValue | object) -> float:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return 0.0


def _to_optional_float(value: JSONValue | object) -> float | None:
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _is_option_contract_symbol(symbol: str) -> bool:
    return bool(_OPTION_CONTRACT_PATTERN.match(symbol.upper().strip()))


def _estimate_delta(*, strike: float, spot: float, dte: int, option_type: str) -> float:
    normalized_type = option_type.strip().lower()
    if strike <= 0 or spot <= 0 or dte <= 0:
        return 0.5 if normalized_type == "call" else -0.5
    vol = 0.30
    time_years = dte / 365.0
    try:
        d1 = math.log(spot / strike) / (vol * math.sqrt(time_years)) + 0.5 * vol * math.sqrt(time_years)
    except (ValueError, ZeroDivisionError):
        return 0.5 if normalized_type == "call" else -0.5
    nd1 = 1.0 / (1.0 + math.exp(-1.7 * d1))
    return nd1 if normalized_type == "call" else nd1 - 1.0
