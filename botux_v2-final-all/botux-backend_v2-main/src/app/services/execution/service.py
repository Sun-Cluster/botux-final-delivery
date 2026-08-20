from __future__ import annotations

from loguru import logger
import asyncio
from datetime import datetime, timedelta, timezone
from typing import cast

from tortoise.backends.base.client import BaseDBAsyncClient

from app.services.intelligence.evaluators import evaluate_time_window, pdt_can_trade
from app.services.runtime_config.service import RuntimeConfigService
from db.repositories.orders_repo import OrdersRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from runtime.logging import format_log_fields
from domain.enums import ExecutionStatus, OrderStatus
from domain.models.execution_result import ExecutionResult
from domain.models.order_intent import OrderIntent
from infra.brokers.base import BrokerPort


pipeline_logger = logger.bind(pipeline_module=__name__)

class ExecutionService:
    def __init__(self, broker: BrokerPort | None = None, *, connection: BaseDBAsyncClient | None = None) -> None:
        self._broker = broker
        self._runtime_configs = RuntimeConfigService(connection=connection)
        self._orders = OrdersRepository(connection=connection)
        self._outcomes = TradeOutcomesRepository(connection=connection)

    async def submit(self, order_id: str, order_intent: OrderIntent) -> ExecutionResult:
        pipeline_logger.log("INFO", "pipeline.{} {}", "execution.submit_started", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "market": order_intent.market, "order_type": order_intent.order_type, "quantity": order_intent.quantity, "broker_connected": self._broker is not None}))
        if self._broker is None:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.submit_unavailable", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name}))
            return ExecutionResult(
                order_id=order_id,
                status=ExecutionStatus.FAILED,
                error_reason="broker_unavailable",
                broker_order_id=None,
                filled_qty=0,
                avg_price=None,
            )

        guardrail_result = await self._check_execution_guardrails(order_id=order_id, order_intent=order_intent)
        if guardrail_result is not None:
            return guardrail_result

        broker_payload = await self._broker.submit_order(order_intent)
        pipeline_logger.log("INFO", "pipeline.{} {}", "broker.submit_result", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "broker_order_id": _pick_text(broker_payload, "broker_order_id", "order_id", "id"), "status": broker_payload.get("status"), "filled_qty": _pick_float(broker_payload, "filled_qty", "qty", "quantity"), "avg_price": _pick_optional_float(broker_payload, "avg_price", "filled_avg_price", "price")}))
        status = _parse_execution_status(broker_payload.get("status"))
        broker_order_id = _pick_text(broker_payload, "broker_order_id", "order_id", "id")
        filled_qty = _pick_float(broker_payload, "filled_qty", "qty", "quantity")
        avg_price = _pick_optional_float(broker_payload, "avg_price", "filled_avg_price", "price")
        if broker_order_id is None and status == ExecutionStatus.SUBMITTED:
            pipeline_logger.log("ERROR", "pipeline.{} {}", "broker.submit_unconfirmed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "status": broker_payload.get("status")}))
            status = ExecutionStatus.FAILED
        error_reason = "broker_submit_unconfirmed" if status == ExecutionStatus.FAILED and broker_order_id is None and _parse_execution_status(broker_payload.get("status")) == ExecutionStatus.SUBMITTED else _execution_error_reason(status=status, payload=broker_payload)
        if broker_order_id is not None and status == ExecutionStatus.SUBMITTED:
            status_payload = await self._poll_for_terminal_or_fill(
                broker_order_id=broker_order_id,
                order_id=order_id,
                order_intent=order_intent,
            )
            if status_payload is not None:
                status = _parse_execution_status(status_payload.get("status"))
                filled_qty = _pick_float(status_payload, "filled_qty", "qty", "quantity")
                avg_price = _pick_optional_float(status_payload, "avg_price", "filled_avg_price", "price")
                error_reason = _execution_error_reason(status=status, payload=status_payload)
                if status == ExecutionStatus.SUBMITTED:
                    pipeline_logger.log("WARNING", "pipeline.{} {}", "broker.submit_unconfirmed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "broker_order_id": broker_order_id}))
                    error_reason = "broker_submit_unconfirmed"
        return ExecutionResult(
            order_id=order_id,
            status=status,
            error_reason=error_reason,
            broker_order_id=broker_order_id,
            filled_qty=filled_qty,
            avg_price=avg_price,
        )

    async def _check_execution_guardrails(
        self,
        *,
        order_id: str,
        order_intent: OrderIntent,
    ) -> ExecutionResult | None:
        if self._broker is None:
            return None
        bypass_risk = await self._runtime_configs.resolve_bool("bypass.risk")
        bypass_market_hours = await self._runtime_configs.resolve_bool("bypass.market_hours")
        enforce = await self._runtime_configs.resolve_bool("execution.enforce_exec_guards")
        max_spread = await self._runtime_configs.resolve_float("execution.max_spread_bps")
        max_signal_age_us = await self._runtime_configs.resolve("execution.max_signal_age_minutes_us")
        max_signal_age_asx = await self._runtime_configs.resolve("execution.max_signal_age_minutes_asx")
        max_signal_price_drift = await self._runtime_configs.resolve_float("execution.max_signal_price_drift_pct")
        if bool(bypass_risk.value):
            pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.risk_bypassed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "config_origin": bypass_risk.origin}))
            _metadata(order_intent)["risk_bypass"] = {
                "enabled": True,
                "origin": bypass_risk.origin,
            }
            return None

        account = await self._broker.get_account()
        entry_flow_result = await self._check_entry_flow_controls(
            order_id=order_id,
            order_intent=order_intent,
            account=account,
        )
        if entry_flow_result is not None:
            return entry_flow_result
        positions, quote = await asyncio.gather(
            self._broker.get_positions(),
            self._broker.get_quote(order_intent.symbol),
        )
        freshness_result = self._check_signal_freshness_and_price_drift(
            order_id=order_id,
            order_intent=order_intent,
            quote=quote,
            max_signal_age_minutes_us=_coerced_int(max_signal_age_us.value, fallback=30),
            max_signal_age_minutes_asx=_coerced_int(max_signal_age_asx.value, fallback=240),
            max_signal_price_drift_pct=_coerced_float(max_signal_price_drift.value, fallback=2.0),
        )
        if freshness_result is not None:
            return freshness_result
        market_hours_result = self._check_market_hours(
            order_id=order_id,
            order_intent=order_intent,
            bypass_market_hours=bypass_market_hours,
        )
        if market_hours_result is not None:
            return market_hours_result
        risk_result = await self._check_runtime_risk_controls(
            order_id=order_id,
            order_intent=order_intent,
            account=account,
            positions=positions,
            quote=quote,
        )
        if risk_result is not None:
            return risk_result

        assessment = _assess_quote_spread(quote, max_spread_bps=_coerced_float(max_spread.value, fallback=12.0))
        pipeline_logger.log("WARNING" if assessment["guardrail"] == "WIDE_SPREAD" else "INFO", "pipeline.{} {}", "execution.guardrails_assessed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "enforce": bool(enforce.value), "config_origin": enforce.origin, "spread_origin": max_spread.origin, "spread_bps": assessment["spread_bps"], "threshold_bps": assessment["threshold_bps"], "guardrail": assessment["guardrail"], "quote_note": assessment["note"]}))
        _metadata(order_intent)["exec_guard"] = assessment
        if assessment["guardrail"] == "WIDE_SPREAD" and bool(enforce.value):
            pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.guardrails_blocked", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "spread_bps": assessment["spread_bps"], "threshold_bps": assessment["threshold_bps"]}))
            return ExecutionResult(
                order_id=order_id,
                status=ExecutionStatus.REJECTED,
                error_reason="wide_spread",
                broker_order_id=None,
                filled_qty=0,
                avg_price=None,
            )
        return None

    async def _check_entry_flow_controls(
        self,
        *,
        order_id: str,
        order_intent: OrderIntent,
        account: dict[str, object],
    ) -> ExecutionResult | None:
        if order_intent.action.value != "buy":
            _metadata(order_intent)["entry_flow"] = {
                "enforced": False,
                "reason": "sell_path",
            }
            return None

        cooldown = await self._runtime_configs.resolve("execution.cooldown_minutes")
        max_trades_live = await self._runtime_configs.resolve("execution.max_trades_per_day")
        max_trades_paper = await self._runtime_configs.resolve("execution.max_trades_per_day_paper")

        account_mode = str(account.get("mode") or "").strip().lower()
        cap_key = "execution.max_trades_per_day_paper" if account_mode == "paper" else "execution.max_trades_per_day"
        max_trades_value = (
            _coerced_int(max_trades_paper.value, fallback=50)
            if cap_key == "execution.max_trades_per_day_paper"
            else _coerced_int(max_trades_live.value, fallback=8)
        )
        now = datetime.now(timezone.utc)
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        cooldown_minutes = _coerced_int(cooldown.value, fallback=20)
        cooldown_cutoff = now - timedelta(minutes=max(cooldown_minutes, 0))

        trades_today, recent_symbol_orders, has_active_order, has_open_outcome = await asyncio.gather(
            self._orders.count_entry_orders_since(
                day_start,
                statuses={OrderStatus.SUBMITTED, OrderStatus.EXECUTED},
            ),
            self._orders.count_symbol_since(
                order_intent.symbol,
                cooldown_cutoff,
                statuses={OrderStatus.SUBMITTED, OrderStatus.EXECUTED},
                action="buy",
            ),
            self._orders.has_active_entry_for_symbol(order_intent.symbol),
            self._outcomes.has_open_symbol(order_intent.symbol),
        )
        assessment = {
            "enforced": True,
            "account_mode": account_mode or "unknown",
            "daily_cap_key": cap_key,
            "daily_cap": max_trades_value,
            "trades_today": trades_today,
            "cooldown_minutes": cooldown_minutes,
            "recent_symbol_orders": recent_symbol_orders,
            "has_active_order": has_active_order,
            "has_open_outcome": has_open_outcome,
        }
        _metadata(order_intent)["entry_flow"] = assessment
        pipeline_logger.log("INFO", "pipeline.{} {}", "execution.entry_flow_assessed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "account_mode": assessment["account_mode"], "daily_cap_key": cap_key, "daily_cap": max_trades_value, "trades_today": trades_today, "cooldown_minutes": cooldown_minutes, "recent_symbol_orders": recent_symbol_orders, "has_active_order": has_active_order, "has_open_outcome": has_open_outcome}))
        block_reason = _entry_flow_block_reason(
            trades_today=trades_today,
            max_trades_per_day=max_trades_value,
            recent_symbol_orders=recent_symbol_orders,
            has_active_order=has_active_order,
            has_open_outcome=has_open_outcome,
        )
        if block_reason is None:
            return None
        pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.entry_flow_blocked", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "reason": block_reason}))
        return ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.REJECTED,
            error_reason=block_reason,
            broker_order_id=None,
            filled_qty=0,
            avg_price=None,
        )

    def _check_signal_freshness_and_price_drift(
        self,
        *,
        order_id: str,
        order_intent: OrderIntent,
        quote: dict[str, object],
        max_signal_age_minutes_us: int,
        max_signal_age_minutes_asx: int,
        max_signal_price_drift_pct: float,
    ) -> ExecutionResult | None:
        metadata = _metadata(order_intent)
        raw_created = metadata.get("signal_scan_timestamp") or metadata.get("signal_created_at")
        created_at = _parse_datetime(raw_created)
        market = (order_intent.market or "").strip().lower()
        is_asx = market == "asx_equities" or order_intent.symbol.upper().endswith(".AX")
        max_age = max_signal_age_minutes_asx if is_asx else max_signal_age_minutes_us
        age_minutes: float | None = None
        freshness_reason: str | None = None
        if created_at is not None:
            age_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60.0
            if age_minutes > max(max_age, 1):
                freshness_reason = f"stale_signal:{age_minutes:.1f}>{max(max_age, 1)}"
        reference_price = _metadata_float(metadata, "signal_reference_price") or _metadata_float(metadata, "signal_price")
        quote_price = _quote_reference_price(quote)
        drift_pct: float | None = None
        drift_reason: str | None = None
        if reference_price is not None and reference_price > 0 and quote_price is not None and quote_price > 0:
            drift_pct = abs(quote_price - reference_price) / reference_price * 100.0
            if drift_pct > max_signal_price_drift_pct:
                drift_reason = f"signal_price_drift:{drift_pct:.2f}>{max_signal_price_drift_pct:.2f}"

        assessment = {
            "signal_timestamp": created_at.isoformat() if created_at is not None else None,
            "age_minutes": round(age_minutes, 4) if age_minutes is not None else None,
            "max_age_minutes": max(max_age, 1),
            "reference_price": reference_price,
            "quote_price": quote_price,
            "drift_pct": round(drift_pct, 4) if drift_pct is not None else None,
            "max_signal_price_drift_pct": max_signal_price_drift_pct,
            "market": market or "unknown",
        }
        _metadata(order_intent)["signal_freshness"] = assessment
        pipeline_logger.log(
            "INFO",
            "pipeline.{} {}",
            "execution.signal_freshness_assessed",
            format_log_fields(
                {
                    "order_id": order_id,
                    "signal_id": order_intent.signal_id,
                    "symbol": order_intent.symbol,
                    "age_minutes": assessment["age_minutes"],
                    "max_age_minutes": assessment["max_age_minutes"],
                    "reference_price": reference_price,
                    "quote_price": quote_price,
                    "drift_pct": assessment["drift_pct"],
                    "max_signal_price_drift_pct": max_signal_price_drift_pct,
                    "market": market or "unknown",
                }
            ),
        )
        reason = freshness_reason or drift_reason
        if reason is None:
            return None
        pipeline_logger.log(
            "WARNING",
            "pipeline.{} {}",
            "execution.signal_freshness_blocked",
            format_log_fields(
                {
                    "order_id": order_id,
                    "signal_id": order_intent.signal_id,
                    "symbol": order_intent.symbol,
                    "reason": reason,
                    "age_minutes": assessment["age_minutes"],
                    "max_age_minutes": assessment["max_age_minutes"],
                    "drift_pct": assessment["drift_pct"],
                    "max_signal_price_drift_pct": max_signal_price_drift_pct,
                }
            ),
        )
        return ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.REJECTED,
            error_reason=reason,
            broker_order_id=None,
            filled_qty=0,
            avg_price=None,
        )

    def _check_market_hours(
        self,
        *,
        order_id: str,
        order_intent: OrderIntent,
        bypass_market_hours: object,
    ) -> ExecutionResult | None:
        market = (order_intent.market or "unknown").strip().lower()
        if market not in {"us_equities", "options_us"}:
            _metadata(order_intent)["market_hours_gate"] = {
                "enforced": False,
                "reason": "not_applicable",
                "market": market,
            }
            return None
        if bool(getattr(bypass_market_hours, "value")):
            _metadata(order_intent)["market_hours_gate"] = {
                "enforced": False,
                "reason": "bypass_market_hours",
                "origin": getattr(bypass_market_hours, "origin"),
                "market": market,
            }
            pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.market_hours_bypassed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "market": market, "config_origin": getattr(bypass_market_hours, "origin")}))
            return None
        window = evaluate_time_window()
        gate = {
            "enforced": True,
            "market": market,
            "allowed": window.allowed,
            "zone": window.zone,
            "reason": window.reason,
        }
        _metadata(order_intent)["market_hours_gate"] = gate
        pipeline_logger.log("INFO" if window.allowed else "WARNING", "pipeline.{} {}", "execution.market_hours_assessed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "market": market, "allowed": window.allowed, "zone": window.zone, "reason": window.reason}))
        if window.allowed:
            return None
        pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.market_hours_blocked", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "market": market, "zone": window.zone, "reason": window.reason}))
        return ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.REJECTED,
            error_reason=str(window.reason or "market_hours_blocked"),
            broker_order_id=None,
            filled_qty=0,
            avg_price=None,
        )

    async def _check_runtime_risk_controls(
        self,
        *,
        order_id: str,
        order_intent: OrderIntent,
        account: dict[str, object],
        positions: list[dict],
        quote: dict[str, object],
    ) -> ExecutionResult | None:
        max_daily_loss = await self._runtime_configs.resolve_float("risk.max_daily_loss_pct")
        risk_per_trade = await self._runtime_configs.resolve_float("risk.risk_per_trade_pct")
        max_position = await self._runtime_configs.resolve_float("risk.max_position_pct")
        max_open_positions = await self._runtime_configs.resolve("risk.max_open_positions")

        equity = _pick_optional_float(account, "equity")
        last_equity = _pick_optional_float(account, "last_equity")
        cash = _pick_optional_float(account, "cash")
        buying_power = _pick_optional_float(account, "buying_power")
        day_trades_used = _coerced_int(account.get("daytrade_count"), fallback=0)
        open_positions = len([row for row in positions if _position_quantity(row) > 0])
        price = _quote_reference_price(quote)
        quantity = float(order_intent.quantity)
        proposed_notional = (
            quantity * price * _contract_multiplier(order_intent.market)
            if price is not None
            else None
        )
        current_symbol_notional = _current_symbol_notional(
            positions=positions,
            symbol=order_intent.symbol,
            market=order_intent.market,
        )
        stop_loss_pct = _metadata_float(_metadata(order_intent), "stop_loss_pct")
        per_trade_risk = (
            proposed_notional * stop_loss_pct
            if proposed_notional is not None and stop_loss_pct is not None
            else None
        )
        daily_loss_pct = _account_daily_loss_pct(equity=equity, last_equity=last_equity)
        max_open_positions_value = _coerced_int(max_open_positions.value, fallback=15)
        pdt_gate = _pdt_gate(
            market=order_intent.market,
            action=order_intent.action.value,
            equity=equity,
            day_trades_used=day_trades_used,
        )

        assessment = {
            "equity": equity,
            "last_equity": last_equity,
            "cash": cash,
            "buying_power": buying_power,
            "daily_loss_pct": daily_loss_pct,
            "max_daily_loss_pct": _coerced_float(max_daily_loss.value, fallback=0.03),
            "day_trades_used": day_trades_used,
            "pdt_allowed": pdt_gate["allowed"],
            "pdt_reason": pdt_gate["reason"],
            "pdt_remaining": pdt_gate["remaining"],
            "open_positions": open_positions,
            "max_open_positions": max_open_positions_value,
            "reference_price": price,
            "proposed_notional": proposed_notional,
            "current_symbol_notional": current_symbol_notional,
            "max_position_pct": _coerced_float(max_position.value, fallback=0.10),
            "stop_loss_pct": stop_loss_pct,
            "per_trade_risk": per_trade_risk,
            "risk_per_trade_pct": _coerced_float(risk_per_trade.value, fallback=0.01),
        }
        _metadata(order_intent)["runtime_risk"] = assessment
        pipeline_logger.log("INFO", "pipeline.{} {}", "execution.risk_controls_assessed", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "cash": cash, "buying_power": buying_power, "daily_loss_pct": daily_loss_pct, "max_daily_loss_pct": max_daily_loss.value, "day_trades_used": day_trades_used, "pdt_allowed": pdt_gate["allowed"], "pdt_remaining": pdt_gate["remaining"], "open_positions": open_positions, "max_open_positions": max_open_positions_value, "proposed_notional": proposed_notional, "current_symbol_notional": current_symbol_notional, "max_position_pct": max_position.value, "per_trade_risk": per_trade_risk, "risk_per_trade_pct": risk_per_trade.value}))

        block_reason = _risk_block_reason(
            equity=equity,
            cash=cash,
            buying_power=buying_power,
            daily_loss_pct=daily_loss_pct,
            max_daily_loss_pct=_coerced_float(max_daily_loss.value, fallback=0.03),
            pdt_allowed=bool(pdt_gate["allowed"]),
            pdt_reason=str(pdt_gate["reason"]),
            open_positions=open_positions,
            max_open_positions=max_open_positions_value,
            proposed_notional=proposed_notional,
            current_symbol_notional=current_symbol_notional,
            max_position_pct=_coerced_float(max_position.value, fallback=0.10),
            per_trade_risk=per_trade_risk,
            risk_per_trade_pct=_coerced_float(risk_per_trade.value, fallback=0.01),
        )
        if block_reason is None:
            return None
        pipeline_logger.log("WARNING", "pipeline.{} {}", "execution.risk_controls_blocked", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "reason": block_reason}))
        return ExecutionResult(
            order_id=order_id,
            status=ExecutionStatus.REJECTED,
            error_reason=block_reason,
            broker_order_id=None,
            filled_qty=0,
            avg_price=None,
        )

    async def _poll_for_terminal_or_fill(
        self,
        *,
        broker_order_id: str,
        order_id: str,
        order_intent: OrderIntent,
    ) -> dict[str, object] | None:
        if self._broker is None:
            return None
        latest: dict[str, object] | None = None
        for attempt in range(1, 4):
            await asyncio.sleep(0.35)
            latest = await self._broker.get_order_status(broker_order_id)
            polled_status = _parse_execution_status(latest.get("status"))
            pipeline_logger.log("INFO", "pipeline.{} {}", "broker.status_polled", format_log_fields({"order_id": order_id, "signal_id": order_intent.signal_id, "symbol": order_intent.symbol, "broker_name": order_intent.broker_name, "broker_order_id": broker_order_id, "attempt": attempt, "status": latest.get("status"), "filled_qty": _pick_float(latest, "filled_qty", "qty", "quantity"), "avg_price": _pick_optional_float(latest, "avg_price", "filled_avg_price", "price")}))
            if polled_status != ExecutionStatus.SUBMITTED:
                return latest
        return latest


def _parse_execution_status(raw_status: object) -> ExecutionStatus:
    if not isinstance(raw_status, str):
        return ExecutionStatus.SUBMITTED
    normalized = raw_status.strip().lower()
    status_map: dict[str, ExecutionStatus] = {
        "submitted": ExecutionStatus.SUBMITTED,
        "new": ExecutionStatus.SUBMITTED,
        "accepted": ExecutionStatus.SUBMITTED,
        "pending_new": ExecutionStatus.SUBMITTED,
        "accepted_for_bidding": ExecutionStatus.SUBMITTED,
        "partial": ExecutionStatus.PARTIAL,
        "partially_filled": ExecutionStatus.PARTIAL,
        "filled": ExecutionStatus.FILLED,
        "executed": ExecutionStatus.EXECUTED,
        "failed": ExecutionStatus.FAILED,
        "error": ExecutionStatus.FAILED,
        "not_found": ExecutionStatus.FAILED,
        "rejected": ExecutionStatus.REJECTED,
        "canceled": ExecutionStatus.CANCELED,
        "cancelled": ExecutionStatus.CANCELED,
        "expired": ExecutionStatus.EXPIRED,
    }
    return status_map.get(normalized, ExecutionStatus.SUBMITTED)


def _execution_error_reason(*, status: ExecutionStatus, payload: dict[str, object] | None) -> str | None:
    if status not in {
        ExecutionStatus.FAILED,
        ExecutionStatus.REJECTED,
        ExecutionStatus.CANCELED,
        ExecutionStatus.EXPIRED,
    }:
        return None
    if isinstance(payload, dict):
        raw = payload.get("error") or payload.get("reason") or payload.get("message")
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                return text
        raw_status = payload.get("status")
        if isinstance(raw_status, str) and raw_status.strip():
            return raw_status.strip().lower()
    return status.value


def _pick_text(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pick_optional_float(payload: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(cast(float | int | str, value))
        except (TypeError, ValueError):
            continue
    return None


def _pick_float(payload: dict[str, object], *keys: str) -> float:
    value = _pick_optional_float(payload, *keys)
    if value is None:
        return 0.0
    return value


def _position_quantity(payload: dict[str, object]) -> float:
    return _pick_float(payload, "quantity", "qty", "position_qty")


def _position_symbol(payload: dict[str, object]) -> str:
    raw = payload.get("symbol")
    if not isinstance(raw, str):
        return ""
    return raw.upper().strip()


def _metadata_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _quote_reference_price(quote: dict[str, object]) -> float | None:
    ask = _pick_optional_float(quote, "ask_price", "ap", "ask")
    bid = _pick_optional_float(quote, "bid_price", "bp", "bid")
    last = _pick_optional_float(quote, "price", "last_price", "last", "close")
    if ask is not None and ask > 0:
        return ask
    if bid is not None and bid > 0:
        return bid
    return last


def _account_daily_loss_pct(*, equity: float | None, last_equity: float | None) -> float | None:
    if equity is None or last_equity is None or last_equity <= 0:
        return None
    change = equity - last_equity
    if change >= 0:
        return 0.0
    return abs(change) / last_equity


def _coerced_int(value: object, *, fallback: int) -> int:
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return fallback


def _coerced_float(value: object, *, fallback: float) -> float:
    try:
        return float(cast(int | float | str, value))
    except (TypeError, ValueError):
        return fallback


def _metadata(order_intent: OrderIntent) -> dict[str, object]:
    return cast(dict[str, object], order_intent.metadata)


def _current_symbol_notional(*, positions: list[dict], symbol: str, market: str | None = None) -> float:
    normalized = symbol.upper().strip()
    total = 0.0
    for row in positions:
        if _position_symbol(row) != normalized:
            continue
        market_value = _pick_optional_float(row, "market_value")
        if market_value is not None:
            total += abs(market_value)
            continue
        qty = abs(_position_quantity(row))
        current_price = _pick_optional_float(row, "current_price", "last_price", "price")
        if current_price is not None:
            total += qty * current_price * _contract_multiplier(market)
    return total


def _pdt_gate(
    *,
    market: str | None,
    action: str,
    equity: float | None,
    day_trades_used: int,
) -> dict[str, object]:
    normalized_market = (market or "").strip().lower()
    normalized_action = action.strip().lower()
    if normalized_market == "asx_equities":
        return {"allowed": True, "remaining": 999, "reason": "asx_bypass"}
    if normalized_market not in {"us_equities", "options_us"}:
        return {"allowed": True, "remaining": 999, "reason": "not_applicable"}
    if normalized_action not in {"buy"}:
        return {"allowed": True, "remaining": 999, "reason": "sell_path"}
    allowed, remaining, reason = pdt_can_trade(equity=equity or 0.0, day_trades_used=day_trades_used)
    return {"allowed": allowed, "remaining": remaining, "reason": reason}


def _entry_flow_block_reason(
    *,
    trades_today: int,
    max_trades_per_day: int,
    recent_symbol_orders: int,
    has_active_order: bool,
    has_open_outcome: bool,
) -> str | None:
    if trades_today >= max_trades_per_day:
        return f"daily_trade_cap:{trades_today}>={max_trades_per_day}"
    if has_open_outcome:
        return "open_trade_exists"
    if has_active_order:
        return "active_order_exists"
    if recent_symbol_orders > 0:
        return f"symbol_cooldown:{recent_symbol_orders}"
    return None


def _risk_block_reason(
    *,
    equity: float | None,
    cash: float | None,
    buying_power: float | None,
    daily_loss_pct: float | None,
    max_daily_loss_pct: float,
    pdt_allowed: bool,
    pdt_reason: str,
    open_positions: int,
    max_open_positions: int,
    proposed_notional: float | None,
    current_symbol_notional: float,
    max_position_pct: float,
    per_trade_risk: float | None,
    risk_per_trade_pct: float,
) -> str | None:
    if proposed_notional is not None and proposed_notional > 0:
        if buying_power is not None and buying_power < proposed_notional:
            return f"insufficient_buying_power:{buying_power:.2f}<{proposed_notional:.2f}"
        if buying_power is None and cash is not None and cash >= 0 and cash < proposed_notional:
            return f"insufficient_cash:{cash:.2f}<{proposed_notional:.2f}"
    if daily_loss_pct is not None and daily_loss_pct >= max_daily_loss_pct:
        return f"daily_loss_limit:{daily_loss_pct:.4f}>={max_daily_loss_pct:.4f}"
    if not pdt_allowed:
        return f"pdt_block:{pdt_reason}"
    if open_positions >= max_open_positions:
        return f"max_open_positions:{open_positions}>={max_open_positions}"
    if equity is not None and equity > 0 and proposed_notional is not None:
        position_pct = (current_symbol_notional + proposed_notional) / equity
        if position_pct > max_position_pct:
            return f"max_position_pct:{position_pct:.4f}>{max_position_pct:.4f}"
    if equity is not None and equity > 0 and per_trade_risk is not None:
        risk_pct = per_trade_risk / equity
        if risk_pct > risk_per_trade_pct:
            return f"risk_per_trade_pct:{risk_pct:.4f}>{risk_per_trade_pct:.4f}"
    return None


def _contract_multiplier(market: str | None) -> float:
    return 100.0 if (market or "").strip().lower() == "options_us" else 1.0


def _assess_quote_spread(quote: dict[str, object], *, max_spread_bps: float) -> dict[str, object]:
    bid = _pick_optional_float(quote, "bid_price", "bp", "bid")
    ask = _pick_optional_float(quote, "ask_price", "ap", "ask")
    result: dict[str, object] = {
        "spread_bps": None,
        "bid": bid,
        "ask": ask,
        "mid": None,
        "quote_ts": _pick_text(quote, "timestamp", "t"),
        "note": "pending",
        "guardrail": "PENDING",
        "threshold_bps": max_spread_bps,
    }
    if bid is None or ask is None:
        result["note"] = "missing_bid_ask"
        result["guardrail"] = "NO_QUOTE"
        return result
    if bid <= 0 or ask <= 0:
        result["note"] = "zero_price"
        result["guardrail"] = "NO_QUOTE"
        return result
    mid = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / mid) * 10000.0 if mid > 0 else 0.0
    result["mid"] = mid
    result["spread_bps"] = round(spread_bps, 2)
    result["note"] = "ok"
    result["guardrail"] = "WIDE_SPREAD" if spread_bps > max_spread_bps else "OK"
    return result


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
