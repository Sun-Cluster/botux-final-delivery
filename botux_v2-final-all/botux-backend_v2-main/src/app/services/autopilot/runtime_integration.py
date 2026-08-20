from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.services.autopilot.policy import AutopilotPolicyService, normalize_policy, validate_policy
from app.services.autopilot.service import AutopilotService
from app.services.autopilot.snapshot import AutopilotSnapshotService
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.autopilot_repo import AutopilotRepository
from db.repositories.bots_repo import BotsRepository
from db.repositories._common import JSONValue
from db.uow import UnitOfWork

if TYPE_CHECKING:
    from runtime.container import Container


class AutopilotRuntimeIntegration:
    def __init__(self) -> None:
        self._policy_service = AutopilotPolicyService()
        self._snapshot_service = AutopilotSnapshotService()
        self._autopilot_service = AutopilotService()

    async def run_cycle(self, *, container: "Container") -> dict[str, object]:
        started_at = datetime.now(timezone.utc)
        policy = await self._policy_service.get_effective_policy()
        normalized_policy = normalize_policy(policy)
        validation = validate_policy(normalized_policy)
        if not validation.valid:
            return {
                "status": "skipped",
                "reason": "invalid_policy",
                "reason_codes": validation.reason_codes,
                "mode": normalized_policy.get("mode"),
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        if not bool(normalized_policy.get("enabled", False)):
            return {
                "status": "skipped",
                "reason": "policy_disabled",
                "mode": normalized_policy.get("mode"),
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

        snapshot = await self._snapshot_service.build_snapshot(container=container, policy=normalized_policy)
        bots = snapshot.get("bot_performance_context", {})
        bots_count = len(bots) if isinstance(bots, dict) else 0

        run_id: int | None = None
        decisions: list[dict[str, object]] = []
        try:
            async with UnitOfWork() as uow:
                repo = AutopilotRepository(connection=uow.connection)
                run = await repo.create_run(
                    policy_id=_as_int(normalized_policy.get("id")),
                    mode=str(normalized_policy.get("mode", "observe")),
                    snapshot=snapshot,
                    bots_count=bots_count,
                )
                run_id = int(run["id"])  # type: ignore[index]
                decisions = self._autopilot_service.evaluate(
                    snapshot=snapshot,
                    policy=normalized_policy,
                    now=started_at,
                )
                if str(normalized_policy.get("mode", "observe")) == "constrained_apply":
                    await self._apply_constrained_actions(
                        decisions=decisions,
                        policy=normalized_policy,
                        repo=BotsRepository(connection=uow.connection),
                    )
                await repo.insert_decisions(
                    run_id=run_id,
                    policy_id=_as_int(normalized_policy.get("id")),
                    rows=decisions,
                )
                await repo.complete_run(run_id=run_id, status="completed")
                await AuditLogsRepository(connection=uow.connection).append(
                    event_type="autopilot.run.completed",
                    actor="autopilot_runtime_integration",
                    payload={
                        "run_id": run_id,
                        "mode": str(normalized_policy.get("mode", "observe")),
                        "bots_count": bots_count,
                        "decisions_count": len(decisions),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
        except Exception as exc:
            if run_id is not None:
                try:
                    async with UnitOfWork() as uow:
                        repo = AutopilotRepository(connection=uow.connection)
                        await repo.complete_run(
                            run_id=run_id,
                            status="failed",
                            error=str(exc)[:400],
                        )
                except Exception:
                    pass
            return {
                "status": "failed",
                "error": str(exc)[:400],
                "run_id": run_id,
                "mode": str(normalized_policy.get("mode", "observe")),
                "bots_count": bots_count,
                "decisions_count": len(decisions),
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

        reason_counts = _reason_counts(decisions)
        recommendation_counts = _recommended_status_counts(decisions)
        return {
            "status": "completed",
            "run_id": run_id,
            "mode": str(normalized_policy.get("mode", "observe")),
            "bots_count": bots_count,
            "decisions_count": len(decisions),
            "applied_count": sum(1 for row in decisions if bool(row.get("applied", False))),
            "recommendation_counts": recommendation_counts,
            "top_reason_codes": reason_counts[:8],
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _apply_constrained_actions(
        self,
        *,
        decisions: list[dict[str, object]],
        policy: dict[str, JSONValue],
        repo: BotsRepository,
    ) -> None:
        for decision in decisions:
            action = str(decision.get("recommended_state", "")).strip().lower()
            if action not in {"active", "shadow"}:
                continue
            bot_id = str(decision.get("bot_id", "")).strip().lower()
            if not bot_id:
                continue
            existing = await repo.get_bot_profile(bot_id)
            if existing is None:
                continue
            updated = dict(existing)
            updated["autopilot_state"] = action
            updated["autopilot_changed_at"] = datetime.now(timezone.utc).isoformat()
            await repo.upsert_bot_profile(bot_id, updated)
            applied_at = datetime.now(timezone.utc).isoformat()
            decision["applied"] = True
            decision["applied_at"] = applied_at
            evidence = decision.get("evidence")
            evidence_dict = dict(evidence) if isinstance(evidence, dict) else {}
            evidence_dict["apply_result"] = {
                "mode": "constrained_apply",
                "bot_id": bot_id,
                "autopilot_state": action,
                "applied_at": applied_at,
            }
            decision["evidence"] = evidence_dict


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _recommended_status_counts(decisions: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in decisions:
        status = str(row.get("recommended_state", "unknown")).strip().lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _reason_counts(decisions: list[dict[str, object]]) -> list[dict[str, JSONValue]]:
    counts: dict[str, int] = {}
    for row in decisions:
        reasons = row.get("reason_codes")
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            key = str(reason).strip().lower()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"reason_code": key, "count": value} for key, value in ordered]
