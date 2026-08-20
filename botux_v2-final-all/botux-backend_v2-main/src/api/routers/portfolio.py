from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_container
from app.services.portfolio.service import PortfolioService
from runtime.container import Container

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.post("/snapshot")
async def capture_snapshot(container: Container = Depends(get_container)) -> dict[str, object]:
    service = PortfolioService(broker=container.broker)
    return await service.snapshot()
