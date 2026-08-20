from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue

from api.deps import get_container
from app.services.control_plane.service import RuntimeControlPlaneService
from app.services.runtime_config.service import (
    RuntimeConfigService,
    coerce_runtime_config_value,
    get_runtime_config_definition,
)
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.system_configs_repo import SystemConfigsRepository
from db.uow import UnitOfWork
from runtime.container import Container
from runtime.health import event_loop_latency_ms

router = APIRouter(prefix="/runtime", tags=["runtime"])


class RuntimeConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: JsonValue
    updated_by: str | None = None


@router.get("/queues")
async def queue_snapshot(container: Container = Depends(get_container)) -> dict[str, int]:
    return container.queue_bus.snapshot_sizes()


@router.get("/metrics")
async def runtime_metrics(container: Container = Depends(get_container)) -> dict[str, object]:
    control_plane = await RuntimeControlPlaneService().snapshot(container)
    scheduler_snapshot = (
        container.scheduler.snapshot()
        if container.scheduler is not None
        else {"enabled": False, "active": False, "job_count": 0, "jobs": []}
    )
    return {
        "queue": container.queue_bus.snapshot_sizes(),
        "event_loop_latency_ms": await event_loop_latency_ms(),
        "scheduler": scheduler_snapshot,
        "workers": _workers_snapshot(container),
        "control_plane": control_plane,
    }


@router.get("/scheduler")
async def runtime_scheduler(container: Container = Depends(get_container)) -> dict[str, object]:
    scheduler_snapshot = (
        container.scheduler.snapshot()
        if container.scheduler is not None
        else {"enabled": False, "active": False, "job_count": 0, "jobs": []}
    )
    return {
        "scheduler": scheduler_snapshot,
        "generated_at": await _checked_time(),
    }


@router.get("/workers")
async def runtime_workers(container: Container = Depends(get_container)) -> dict[str, object]:
    control_plane = await RuntimeControlPlaneService().snapshot(container)
    return {
        "workers": _workers_snapshot(container),
        "queue": container.queue_bus.snapshot_sizes(),
        "duties": control_plane["duties"],
        "alerts": control_plane["alerts"],
        "generated_at": await _checked_time(),
    }


@router.get("/configs")
async def runtime_configs() -> dict[str, object]:
    rows = await RuntimeConfigService().list_effective()
    return {
        "items": [
            {
                "key": row.key,
                "value": row.value,
                "value_type": row.value_type,
                "origin": row.origin,
                "env_name": row.env_name,
                "description": row.description,
                "scope": row.scope,
            }
            for row in rows
        ],
        "generated_at": await _checked_time(),
    }


@router.put("/configs/{key:path}")
async def runtime_update_config(key: str, request: RuntimeConfigUpdateRequest) -> dict[str, object]:
    try:
        definition = get_runtime_config_definition(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    normalized_value = coerce_runtime_config_value(key, request.value)
    actor = (request.updated_by or "api.runtime").strip() or "api.runtime"
    async with UnitOfWork() as uow:
        repo = SystemConfigsRepository(connection=uow.connection)
        audit_repo = AuditLogsRepository(connection=uow.connection)
        updated = await repo.upsert(
            key=key,
            value=normalized_value,
            value_type=definition.value_type,
            scope=definition.scope,
            description=definition.description,
            updated_by=actor,
        )
        await audit_repo.append(
            event_type="runtime_config.updated",
            actor=actor,
            trace_id=key,
            payload={
                "key": key,
                "value": normalized_value,
                "value_type": definition.value_type,
                "scope": definition.scope,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    resolved = await RuntimeConfigService().resolve(key)
    return {
        "item": {
            "key": resolved.key,
            "value": resolved.value,
            "value_type": resolved.value_type,
            "origin": resolved.origin,
            "env_name": resolved.env_name,
            "description": resolved.description,
            "scope": resolved.scope,
        },
        "stored": updated,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _workers_snapshot(container: Container) -> dict[str, object]:
    if container.process_manager is None:
        return {"running": False, "active_workers": 0, "workers": [], "cpu_pool": {"max_workers": 0}}
    return dict(container.process_manager.snapshot())


async def _checked_time() -> str:
    # keep this async endpoint family uniform with other runtime handlers
    _ = await event_loop_latency_ms(sample_seconds=0.0)
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
