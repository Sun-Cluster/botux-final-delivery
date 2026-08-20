from __future__ import annotations

from datetime import datetime, timezone

from db.models import AutopilotDecision, AutopilotPolicy, AutopilotRun
from db.repositories._common import JSONValue
from db.repositories.base import RepositoryBase


class AutopilotRepository(RepositoryBase):
    async def get_policy(self, policy_id: int) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotPolicy.filter(id=policy_id)).first()
        return None if row is None else _policy_payload(row)

    async def get_policy_by_name(self, name: str) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotPolicy.filter(name=name)).first()
        return None if row is None else _policy_payload(row)

    async def get_active_policy(self) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotPolicy.filter(enabled=True)).order_by("-updated_at").first()
        return None if row is None else _policy_payload(row)

    async def upsert_policy(
        self,
        *,
        name: str,
        enabled: bool,
        mode: str,
        evaluation_window_days: int,
        shadow_min_closed_trades: int,
        shadow_max_win_rate: float,
        shadow_max_pnl_pct: float,
        reactivate_interval_seconds: int,
        reactivate_min_closed_trades: int,
        reactivate_min_win_rate: float,
        reactivate_min_pnl_pct: float,
    ) -> dict[str, JSONValue]:
        row = await self._query(AutopilotPolicy.filter(name=name)).first()
        if row is None:
            row = AutopilotPolicy(name=name)
        row.enabled = bool(enabled)
        row.mode = mode
        row.evaluation_window_days = max(1, int(evaluation_window_days))
        row.shadow_min_closed_trades = max(1, int(shadow_min_closed_trades))
        row.shadow_max_win_rate = float(shadow_max_win_rate)
        row.shadow_max_pnl_pct = float(shadow_max_pnl_pct)
        row.reactivate_interval_seconds = max(0, int(reactivate_interval_seconds))
        row.reactivate_min_closed_trades = max(1, int(reactivate_min_closed_trades))
        row.reactivate_min_win_rate = float(reactivate_min_win_rate)
        row.reactivate_min_pnl_pct = float(reactivate_min_pnl_pct)
        await self._save(row)
        return _policy_payload(row)

    async def create_run(
        self,
        *,
        policy_id: int | None,
        mode: str,
        snapshot: dict[str, JSONValue],
        bots_count: int,
        started_at: datetime | None = None,
    ) -> dict[str, JSONValue]:
        started = started_at or datetime.now(timezone.utc)
        row = AutopilotRun(
            policy_id=policy_id,
            mode=mode,
            snapshot=snapshot,
            bots_count=max(0, int(bots_count)),
            started_at=started,
            status="running",
        )
        await self._save(row)
        return _run_payload(row)

    async def complete_run(
        self,
        *,
        run_id: int,
        status: str,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotRun.filter(id=run_id)).first()
        if row is None:
            return None
        row.status = status.strip().lower()
        row.error = (error or "").strip() or None
        row.completed_at = completed_at or datetime.now(timezone.utc)
        await self._save(row)
        return _run_payload(row)

    async def get_run(self, run_id: int) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotRun.filter(id=run_id)).first()
        return None if row is None else _run_payload(row)

    async def latest_run(self) -> dict[str, JSONValue] | None:
        row = await self._query(AutopilotRun.all()).order_by("-started_at").first()
        return None if row is None else _run_payload(row)

    async def list_runs(self, *, limit: int = 50, before_id: int | None = None) -> list[dict[str, JSONValue]]:
        safe_limit = max(1, min(limit, 200))
        query = self._query(AutopilotRun.all())
        if before_id is not None:
            query = query.filter(id__lt=before_id)
        rows = await query.order_by("-id").limit(safe_limit)
        return [_run_payload(row) for row in rows]

    async def insert_decisions(
        self,
        *,
        run_id: int,
        policy_id: int | None,
        rows: list[dict[str, object]],
    ) -> list[dict[str, JSONValue]]:
        inserted: list[dict[str, JSONValue]] = []
        for row in rows:
            record = AutopilotDecision(
                run_id=run_id,
                policy_id=policy_id,
                bot_id=str(row.get("bot_id", "")).strip().lower(),
                previous_state=str(row.get("previous_state", "active")).strip().lower(),
                recommended_state=str(row.get("recommended_state", "active")).strip().lower(),
                reason_codes=_as_lower_list(row.get("reason_codes")),
                evidence=_as_json_dict(row.get("evidence")),
                applied=bool(row.get("applied", False)),
                applied_at=_as_datetime_or_none(row.get("applied_at")),
            )
            await self._save(record)
            inserted.append(_decision_payload(record))
        return inserted

    async def list_decisions(
        self,
        *,
        limit: int = 100,
        run_id: int | None = None,
        bot_id: str | None = None,
        recommended_state: str | None = None,
    ) -> list[dict[str, JSONValue]]:
        safe_limit = max(1, min(limit, 500))
        query = self._query(AutopilotDecision.all())
        if run_id is not None:
            query = query.filter(run_id=run_id)
        if bot_id is not None and bot_id.strip():
            query = query.filter(bot_id=bot_id.strip().lower())
        if recommended_state is not None and recommended_state.strip():
            query = query.filter(recommended_state=recommended_state.strip().lower())
        rows = await query.order_by("-created_at").limit(safe_limit)
        return [_decision_payload(row) for row in rows]

    async def latest_decision_for_subject(
        self,
        *,
        bot_id: str,
    ) -> dict[str, JSONValue] | None:
        row = await self._query(
            AutopilotDecision.filter(
                bot_id=bot_id.strip().lower(),
            )
        ).order_by("-created_at").first()
        return None if row is None else _decision_payload(row)

    async def latest_decision_index(self) -> dict[str, dict[str, JSONValue]]:
        rows = await self._query(AutopilotDecision.all()).order_by("-created_at")
        result: dict[str, dict[str, JSONValue]] = {}
        for row in rows:
            key = str(row.bot_id).strip().lower()
            if key in result:
                continue
            result[key] = _decision_payload(row)
        return result


def _policy_payload(row: AutopilotPolicy) -> dict[str, JSONValue]:
    return {
        "id": int(row.id),
        "name": row.name,
        "enabled": bool(row.enabled),
        "mode": row.mode,
        "evaluation_window_days": int(row.evaluation_window_days),
        "shadow_min_closed_trades": int(row.shadow_min_closed_trades),
        "shadow_max_win_rate": float(row.shadow_max_win_rate),
        "shadow_max_pnl_pct": float(row.shadow_max_pnl_pct),
        "reactivate_interval_seconds": int(row.reactivate_interval_seconds),
        "reactivate_min_closed_trades": int(row.reactivate_min_closed_trades),
        "reactivate_min_win_rate": float(row.reactivate_min_win_rate),
        "reactivate_min_pnl_pct": float(row.reactivate_min_pnl_pct),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _run_payload(row: AutopilotRun) -> dict[str, JSONValue]:
    return {
        "id": int(row.id),
        "policy_id": None if row.policy_id is None else int(row.policy_id),
        "mode": row.mode,
        "snapshot": _as_json_dict(row.snapshot),
        "bots_count": int(row.bots_count),
        "started_at": row.started_at.isoformat(),
        "completed_at": None if row.completed_at is None else row.completed_at.isoformat(),
        "status": row.status,
        "error": row.error,
    }


def _decision_payload(row: AutopilotDecision) -> dict[str, JSONValue]:
    return {
        "id": int(row.id),
        "run_id": int(row.run_id),
        "policy_id": None if row.policy_id is None else int(row.policy_id),
        "bot_id": row.bot_id,
        "previous_state": row.previous_state,
        "recommended_state": row.recommended_state,
        "reason_codes": [str(item) for item in (row.reason_codes or [])],
        "evidence": _as_json_dict(row.evidence),
        "applied": bool(row.applied),
        "applied_at": None if row.applied_at is None else row.applied_at.isoformat(),
        "created_at": row.created_at.isoformat(),
    }


def _as_json_dict(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, JSONValue] = {}
    for key, item in value.items():
        coerced = _as_json_value(item)
        if coerced is not None or item is None:
            result[str(key)] = coerced
    return result


def _as_json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_as_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_as_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_json_value(item) for key, item in value.items()}
    return str(value)

def _as_lower_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def _as_datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
