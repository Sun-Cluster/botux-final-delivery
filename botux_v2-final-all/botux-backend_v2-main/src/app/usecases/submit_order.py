from __future__ import annotations

from loguru import logger
from datetime import datetime, timezone
from typing import cast

from pydantic import JsonValue

from app.services.execution.service import ExecutionService
from app.services.gate.service import GateService
from app.services.runtime_config.service import RuntimeConfigService
from app.services.signals.ownership import infer_execution_bot_id, infer_origin_bot_id
from runtime.logging import format_log_fields
from db.repositories._common import JSONValue
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.council_repo import CouncilRepository
from db.repositories.executions_repo import ExecutionsRepository
from db.repositories.orders_repo import OrdersRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.enums import CouncilDecision, ExecutionStatus, SignalStatus
from domain.models.execution_result import ExecutionResult
from domain.models.gate_decision import GateDecision
from domain.models.order_intent import OrderIntent
from domain.models.signal import Signal
from infra.brokers.router import BrokerRoute, BrokerRouter, infer_bot_id, infer_market


pipeline_logger = logger.bind(pipeline_module=__name__)

async def submit_order(
    signal: Signal,
    *,
    quantity: float = 1.0,
    gate_service: GateService | None = None,
    execution_service: ExecutionService | None = None,
    broker_router: BrokerRouter | None = None,
) -> ExecutionResult | None:
    gate = gate_service or GateService()
    pipeline_logger.log("INFO", "pipeline.{} {}", "signal.submit_started", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "source": signal.source, "lane": signal.lane_hint, "strategy": signal.strategy_hint, "quantity": quantity}))
    async with UnitOfWork() as uow:
        signals_repo = SignalsRepository(connection=uow.connection)
        bots_repo = BotsRepository(connection=uow.connection)
        council_repo = CouncilRepository(connection=uow.connection)
        orders_repo = OrdersRepository(connection=uow.connection)
        executions_repo = ExecutionsRepository(connection=uow.connection)
        outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
        audit_repo = AuditLogsRepository(connection=uow.connection)
        runtime_configs = RuntimeConfigService(connection=uow.connection)

        profile_gate_reason = await _profile_gate_reason(
            signal=signal,
            bots_repo=bots_repo,
            runtime_configs=runtime_configs,
            audit_repo=audit_repo,
        )
        if profile_gate_reason is not None:
            signal.blocked_reason = profile_gate_reason

        decision = await _resolve_gate_decision(
            signal=signal,
            gate=gate,
            runtime_configs=runtime_configs,
            audit_repo=audit_repo,
        )
        await _log_council_and_risk_detail(
            signal=signal,
            decision=decision,
            audit_repo=audit_repo,
        )
        await council_repo.save_decision(decision)
        if decision.decision != CouncilDecision.APPROVE:
            await signals_repo.set_status(signal.signal_id, SignalStatus.REJECTED, reason=decision.reason)
            pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "decision": decision.decision.value, "reason": decision.reason, "failures": getattr(decision, "failures", None)}))
            return None

        await signals_repo.set_status(signal.signal_id, SignalStatus.APPROVED, reason=decision.reason)
        route = broker_router.plan(signal) if broker_router is not None else None
        fallback_bot_id = infer_bot_id(signal)
        pipeline_logger.log("INFO", "pipeline.{} {}", "signal.approved", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "decision": decision.decision.value, "reason": decision.reason, "broker_name": None if route is None else route.broker_name, "market": infer_market(signal, bot_id=fallback_bot_id) if route is None else route.market, "order_type": "market" if route is None else route.order_type}))
        order_intent = OrderIntent(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            action=signal.action,
            quantity=quantity,
            idempotency_key=f"{signal.signal_id}:order",
            broker_name=None if route is None else route.broker_name,
            market=infer_market(signal, bot_id=fallback_bot_id) if route is None else route.market,
            order_type="market" if route is None else route.order_type,
            lane_hint=signal.lane_hint,
            strategy_hint=signal.strategy_hint,
            metadata=_build_order_metadata(signal=signal, decision=decision, route=route),
        )
        order_id = await orders_repo.create_order_intent(order_intent)
        pipeline_logger.log("INFO", "pipeline.{} {}", "order.requested", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "order_id": order_id, "broker_name": order_intent.broker_name, "market": order_intent.market, "order_type": order_intent.order_type, "quantity": order_intent.quantity}))
        execution = execution_service or ExecutionService(
            broker=None if route is None else route.broker,
            connection=uow.connection,
        )
        execution_result = await execution.submit(order_id, order_intent)
        await executions_repo.save_execution(execution_result)
        pipeline_logger.log("WARNING"
            if execution_result.status in {ExecutionStatus.FAILED, ExecutionStatus.REJECTED, ExecutionStatus.CANCELED, ExecutionStatus.EXPIRED}
            else "INFO", "pipeline.{} {}", "execution.result", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "order_id": order_id, "status": execution_result.status.value, "broker_order_id": execution_result.broker_order_id, "filled_qty": execution_result.filled_qty, "avg_price": execution_result.avg_price}))
        if execution_result.status in {ExecutionStatus.FILLED, ExecutionStatus.EXECUTED}:
            await outcomes_repo.record_execution_entry(execution_result)
        return execution_result


def _build_order_metadata(
    *,
    signal: Signal,
    decision: GateDecision,
    route: BrokerRoute | None,
) -> dict[str, JsonValue]:
    metadata = dict(signal.metadata)
    metadata["signal_source"] = signal.source
    metadata["signal_score"] = signal.score
    metadata["signal_created_at"] = signal.created_at.isoformat()
    metadata["signal_scan_timestamp"] = (
        signal.scan_timestamp.isoformat() if signal.scan_timestamp is not None else None
    )
    if signal.confidence is not None:
        metadata["signal_confidence"] = signal.confidence
    signal_price = _resolve_signal_reference_price(signal.metadata)
    if signal_price is not None:
        metadata["signal_reference_price"] = signal_price
    if route is not None:
        metadata["bot_id"] = getattr(route, "bot_id")
        metadata["route_reason"] = getattr(route, "route_reason")
        metadata["allowed_brokers"] = list(getattr(route, "allowed_brokers"))
        metadata["order_types_required"] = list(getattr(route, "order_types_required"))
    position_size_pct = getattr(decision, "position_size_pct", None)
    stop_loss_pct = getattr(decision, "stop_loss_pct", None)
    take_profit_pct = getattr(decision, "take_profit_pct", None)
    if position_size_pct is not None:
        metadata["position_size_pct"] = position_size_pct
    if stop_loss_pct is not None:
        metadata["stop_loss_pct"] = stop_loss_pct
    if take_profit_pct is not None:
        metadata["take_profit_pct"] = take_profit_pct
    return metadata


async def _resolve_gate_decision(
    *,
    signal: Signal,
    gate: GateService,
    runtime_configs: RuntimeConfigService,
    audit_repo: AuditLogsRepository,
) -> GateDecision:
    bypass_council = await runtime_configs.resolve_bool("bypass.council")

    if bool(bypass_council.value):
        signal.metadata["governance_bypass_council"] = True
        signal.metadata["governance_bypass_origin"] = bypass_council.origin
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.council_bypassed", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "config_origin": bypass_council.origin}))
        await audit_repo.append(
            event_type="runtime_config.bypass_council_used",
            trace_id=signal.signal_id,
            actor="runtime",
            payload={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "config_key": bypass_council.key,
                "origin": bypass_council.origin,
            },
        )
        return _synthetic_approval(signal=signal, reason="bypass_council")

    return await gate.evaluate(signal)


async def _profile_gate_reason(
    *,
    signal: Signal,
    bots_repo: BotsRepository,
    runtime_configs: RuntimeConfigService,
    audit_repo: AuditLogsRepository,
) -> str | None:
    bypass_lifecycle = await runtime_configs.resolve_bool("bypass.bot_lifecycle")
    bot_id = infer_execution_bot_id(signal)
    signal.metadata.setdefault("origin_bot_id", infer_origin_bot_id(signal))
    signal.metadata.setdefault("execution_bot_id", bot_id)
    signal.metadata.setdefault("bot_id", bot_id)

    if bool(bypass_lifecycle.value):
        signal.metadata["bypass_bot_lifecycle"] = True
        signal.metadata["bypass_bot_lifecycle_origin"] = bypass_lifecycle.origin
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_bypassed", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "reason": "bypass_bot_lifecycle", "config_origin": bypass_lifecycle.origin}))
        await audit_repo.append(
            event_type="runtime_config.bypass_bot_lifecycle_used",
            trace_id=signal.signal_id,
            actor="runtime",
            payload={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "bot_id": bot_id,
                "config_key": bypass_lifecycle.key,
                "origin": bypass_lifecycle.origin,
            },
        )
        return None

    if bot_id in {"unknown", "manual"}:
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "reason": "bot_identity_unresolved"}))
        return "bot_identity_unresolved"

    profile = await bots_repo.get_bot_profile(bot_id)
    if profile is None:
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "reason": "bot_profile_missing"}))
        return "bot_profile_missing"

    enabled = bool(profile.get("enabled", False))
    lifecycle = str(profile.get("lifecycle_state", "unknown")).strip().lower()
    autopilot_state = str(profile.get("autopilot_state", "active")).strip().lower() or "active"
    signal.metadata["bot_enabled"] = enabled
    signal.metadata["bot_lifecycle_state"] = lifecycle
    signal.metadata["bot_autopilot_state"] = autopilot_state

    allowed_lifecycle = {"paper", "live", "scaled"}
    if not enabled:
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "lifecycle_state": lifecycle, "reason": "bot_disabled"}))
        return "bot_disabled"
    if lifecycle not in allowed_lifecycle:
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "lifecycle_state": lifecycle, "reason": "bot_lifecycle_not_executable"}))
        return "bot_lifecycle_not_executable"
    if autopilot_state == "shadow":
        pipeline_logger.log("WARNING", "pipeline.{} {}", "signal.profile_gate_rejected", format_log_fields({"signal_id": signal.signal_id, "symbol": signal.symbol, "bot_id": bot_id, "autopilot_state": autopilot_state, "reason": "bot_autopilot_shadow"}))
        return "bot_autopilot_shadow"
    return None


def _synthetic_approval(*, signal: Signal, reason: str) -> GateDecision:
    return GateDecision(
        signal_id=signal.signal_id,
        decision=CouncilDecision.APPROVE,
        reason=reason,
        confidence=signal.confidence or signal.score,
        buy_votes=0.0,
        total_votes=0,
        approval_score=signal.confidence or signal.score,
        evidence={
            "mode": "synthetic_approval",
            "reason": reason,
        },
        created_at=datetime.now(timezone.utc),
    )


async def _log_council_and_risk_detail(
    *,
    signal: Signal,
    decision: GateDecision,
    audit_repo: AuditLogsRepository,
) -> None:
    vote_rows: list[dict[str, JSONValue]] = []
    for vote in decision.votes:
        vote_payload: dict[str, JSONValue] = {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "source": signal.source,
            "voter": vote.voter,
            "vote": vote.vote,
            "confidence": float(vote.confidence),
            "weight": float(vote.weight),
            "veto": bool(vote.veto),
            "reasoning": vote.reasoning,
            "evidence": _json_dict(vote.evidence),
            "created_at": vote.created_at.isoformat(),
        }
        vote_rows.append(vote_payload)
        pipeline_logger.log(
            "INFO",
            "pipeline.{} {}",
            "council.vote",
            format_log_fields(
                {
                    "signal_id": signal.signal_id,
                    "symbol": signal.symbol,
                    "voter": vote.voter,
                    "vote": vote.vote,
                    "confidence": round(float(vote.confidence), 4),
                    "weight": round(float(vote.weight), 4),
                    "veto": bool(vote.veto),
                    "reasoning": vote.reasoning,
                }
            ),
        )

    failure_rows: list[dict[str, JSONValue]] = []
    for failure in decision.failures:
        payload = _json_dict(failure.payload)
        failure_payload: dict[str, JSONValue] = {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "source": signal.source,
            "gate_name": failure.gate_name,
            "reason": failure.reason,
            "veto": bool(failure.veto),
            "payload": payload,
        }
        failure_rows.append(failure_payload)
        pipeline_logger.log(
            "WARNING",
            "pipeline.{} {}",
            "risk.failure",
            format_log_fields(
                {
                    "signal_id": signal.signal_id,
                    "symbol": signal.symbol,
                    "gate_name": failure.gate_name,
                    "reason": failure.reason,
                    "veto": bool(failure.veto),
                    "blocked_reason": payload.get("blocked_reason"),
                    "trading_halted": payload.get("trading_halted"),
                    "consecutive_losses": payload.get("consecutive_losses"),
                    "correlation_blocked": payload.get("correlation_blocked"),
                    "pdt_allowed": payload.get("pdt_allowed"),
                }
            ),
        )

    decision_payload: dict[str, JSONValue] = {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "source": signal.source,
        "decision": decision.decision.value,
        "reason": decision.reason,
        "confidence": float(decision.confidence),
        "buy_votes": float(decision.buy_votes),
        "total_votes": int(decision.total_votes),
        "vetoed": bool(decision.vetoed),
        "veto_reason": decision.veto_reason,
        "approval_score": float(decision.approval_score) if decision.approval_score is not None else None,
        "position_size_pct": float(decision.position_size_pct) if decision.position_size_pct is not None else None,
        "stop_loss_pct": float(decision.stop_loss_pct) if decision.stop_loss_pct is not None else None,
        "take_profit_pct": float(decision.take_profit_pct) if decision.take_profit_pct is not None else None,
        "votes": vote_rows,
        "failures": failure_rows,
        "evidence": _json_dict(decision.evidence),
        "schema_version": decision.schema_version,
        "created_at": decision.created_at.isoformat(),
    }
    await audit_repo.append(
        event_type="council.decision_detail",
        trace_id=signal.signal_id,
        actor="gate_service",
        payload=decision_payload,
    )
    pipeline_logger.log(
        "INFO" if decision.decision.value == "approve" else "WARNING",
        "pipeline.{} {}",
        "council.decision",
        format_log_fields(
            {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "confidence": round(float(decision.confidence), 4),
                "buy_votes": round(float(decision.buy_votes), 4),
                "total_votes": int(decision.total_votes),
                "vetoed": bool(decision.vetoed),
                "votes_count": len(vote_rows),
                "failures_count": len(failure_rows),
            }
        ),
    )


def _resolve_signal_reference_price(metadata: dict[str, JsonValue]) -> float | None:
    direct = _as_float(metadata.get("signal_price")) or _as_float(metadata.get("reference_price"))
    if direct is not None:
        return direct
    candidate = metadata.get("candidate")
    if isinstance(candidate, dict):
        return _as_float(candidate.get("reference_price")) or _as_float(candidate.get("price"))
    return None


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(float | int | str, value))
    except (TypeError, ValueError):
        return None


def _json_dict(value: dict[str, object]) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {}
    for key, item in value.items():
        payload[key] = _json_value(item)
    return payload


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
