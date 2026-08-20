from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_container
from app.services.control_plane.service import RuntimeControlPlaneService
from runtime.container import Container

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(container: Container = Depends(get_container)) -> dict:
    runtime = await RuntimeControlPlaneService().snapshot(container)
    return {
        "status": "ok",
        "env": container.config.env,
        "runtime_mode": "asyncio",
        "db_driver": "tortoise",
        "runtime": runtime,
    }
