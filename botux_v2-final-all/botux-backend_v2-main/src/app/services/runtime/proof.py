from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from db.models import OrderRecord
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork


class RuntimeProofService:
    async def build_runtime_pack(
        self,
        *,
        container: object | None = None,
        window_minutes: int = 120,
    ) -> dict[str, object]:
        safe_window = max(15, int(window_minutes))
        since = datetime.now(timezone.utc) - timedelta(minutes=safe_window)
        scheduler = _scheduler_snapshot(container)

        async with UnitOfWork() as uow:
            signals_repo = SignalsRepository(connection=uow.connection)
            outcomes_repo = TradeOutcomesRepository(connection=uow.connection)
            bots_repo = BotsRepository(connection=uow.connection)
            audit_repo = AuditLogsRepository(connection=uow.connection)

            signals = await signals_repo.list_recent(limit=800)
            recent_exit_signals = [
                signal
                for signal in signals
                if _signal_is_exit(signal) and signal.created_at is not None and signal.created_at >= since
            ]
            open_rows = await outcomes_repo.list_open_rows(limit=2000)
            closed_rows = await outcomes_repo.list_closed_rows(limit=800)
            recent_closed = [
                row
                for row in closed_rows
                if row.closed_at is not None and row.closed_at >= since
            ]
            profiles = await bots_repo.list_bot_profiles()
            recent_exit_orders = await OrderRecord.filter(
                action="sell",
                created_at__gte=since,
            ).order_by("-created_at").limit(800)
            auto_exit_events = await audit_repo.list_recent_by_prefix(
                prefix="proof.auto_exit.",
                limit=1500,
            )

        exit_symbols = {signal.symbol.upper() for signal in recent_exit_signals if signal.symbol}
        unresolved_symbols = sorted(
            row.symbol.upper()
            for row in open_rows
            if row.symbol and row.symbol.upper() in exit_symbols
        )
        by_lane: dict[str, dict[str, int]] = defaultdict(lambda: {"signals": 0, "orders": 0, "closed": 0})
        recent_auto_exits = [
            row
            for row in auto_exit_events
            if row.created_at is not None and row.created_at >= since
        ]
        for signal in recent_exit_signals:
            lane = str(signal.lane_hint or signal.source or "unknown").strip().lower() or "unknown"
            by_lane[lane]["signals"] += 1
        for order in recent_exit_orders:
            lane = str(order.signal_source or "unknown").strip().lower() or "unknown"
            by_lane[lane]["orders"] += 1
        for row in recent_closed:
            lane = str(row.source or "unknown").strip().lower() or "unknown"
            by_lane[lane]["closed"] += 1
        submitted_auto_exits = 0
        for row in recent_auto_exits:
            payload = row.payload if isinstance(row.payload, dict) else {}
            result = str(payload.get("result") or "").strip().lower()
            if result == "submitted":
                submitted_auto_exits += 1

        bot_rows = [_bot_runtime_row(bot_id, profile, scheduler_active=scheduler["active"]) for bot_id, profile in profiles.items()]
        active_bots = sum(1 for row in bot_rows if row["runtime_status"] == "active")
        inactive_bots = sum(1 for row in bot_rows if row["runtime_status"] == "inactive")
        offline_bots = sum(1 for row in bot_rows if row["runtime_status"] == "offline")

        return {
            "schema_version": "runtime_proof.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_minutes": safe_window,
            "scheduler": {
                "active": scheduler["active"],
                "enabled": scheduler["enabled"],
                "job_count": scheduler["job_count"],
                "failing_jobs": scheduler["failing_jobs"],
            },
            "bots": {
                "count": len(bot_rows),
                "active": active_bots,
                "inactive": inactive_bots,
                "offline": offline_bots,
                "rows": bot_rows,
            },
            "exits": {
                "open_positions": len(open_rows),
                "recent_exit_signals": len(recent_exit_signals),
                "recent_exit_orders": len(recent_exit_orders),
                "recent_outcomes_closed": len(recent_closed),
                "recent_auto_exit_events": len(recent_auto_exits),
                "recent_auto_exit_submitted": submitted_auto_exits,
                "unresolved_symbols": unresolved_symbols,
                "by_lane": dict(by_lane),
            },
        }

    async def persist_runtime_pack(self, *, payload: dict[str, object]) -> int:
        async with UnitOfWork() as uow:
            repo = AuditLogsRepository(connection=uow.connection)
            return await repo.append(
                event_type="proof.runtime.pack",
                actor="runtime_proof_service",
                payload=_json_payload(payload),
            )


def _signal_is_exit(signal: object) -> bool:
    raw_action = getattr(signal, "action", "")
    action = str(getattr(raw_action, "value", raw_action)).strip().lower()
    if action != "sell":
        return False
    lane_hint = str(getattr(signal, "lane_hint", "")).strip().lower()
    strategy_hint = str(getattr(signal, "strategy_hint", "")).strip().lower()
    source = str(getattr(signal, "source", "")).strip().lower()
    metadata = getattr(signal, "metadata", {})
    exit_reason = metadata.get("exit_reason") if isinstance(metadata, dict) else None
    if exit_reason is not None:
        return True
    return (
        strategy_hint == "position_exit"
        or lane_hint in {"tradecopy", "options", "swingtrade"}
        or source in {"tradecopy", "options", "swingtrade"}
    )


def _scheduler_snapshot(container: object | None) -> dict[str, object]:
    scheduler = getattr(container, "scheduler", None) if container is not None else None
    if scheduler is None:
        return {"enabled": False, "active": False, "job_count": 0, "failing_jobs": []}
    snapshot = scheduler.snapshot()
    jobs = snapshot.get("jobs")
    failing_jobs: list[dict[str, object]] = []
    if isinstance(jobs, list):
        for row in jobs:
            if not isinstance(row, dict):
                continue
            error = row.get("last_error")
            if isinstance(error, str) and error.strip():
                failing_jobs.append({"name": row.get("name"), "last_error": error[:200]})
    return {
        "enabled": bool(snapshot.get("enabled")),
        "active": bool(snapshot.get("active")),
        "job_count": int(snapshot.get("job_count", 0) or 0),
        "failing_jobs": failing_jobs,
    }


def _bot_runtime_row(bot_id: str, profile: dict[str, object], *, scheduler_active: bool) -> dict[str, object]:
    lifecycle = str(profile.get("lifecycle_state") or "unknown").strip().lower()
    enabled = bool(profile.get("enabled", False))
    runtime_status = _runtime_status(lifecycle=lifecycle, enabled=enabled, scheduler_active=scheduler_active)
    return {
        "bot_id": bot_id,
        "enabled": enabled,
        "lifecycle_state": lifecycle,
        "runtime_status": runtime_status,
    }


def _runtime_status(*, lifecycle: str, enabled: bool, scheduler_active: bool) -> str:
    if lifecycle in {"offline", "retired", "demoted"}:
        return "offline"
    if not enabled:
        return "inactive"
    if scheduler_active:
        return "active"
    return "idle"


def _json_payload(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return {}


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
