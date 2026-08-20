from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_container
from app.services.signals.service import SignalService
from app.usecases.process_pending_signals import process_pending_signals
from app.usecases.requeue_signal import requeue_signal
from domain.models.signal import Signal
from runtime.container import Container

router = APIRouter(prefix="/signals", tags=["signals"])


class IngestSignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    enqueued: bool
    signal_id: str


@router.post("/ingest", response_model=IngestSignalResponse)
async def ingest_signal(
    signal: Signal,
    enqueue: bool = Query(default=True),
    container: Container = Depends(get_container),
) -> IngestSignalResponse:
    service = SignalService()
    await service.ingest_signal(signal)

    enqueued = False
    if enqueue and container.process_manager is not None:
        await container.process_manager.publish_signal(signal)
        enqueued = True

    return IngestSignalResponse(accepted=True, enqueued=enqueued, signal_id=signal.signal_id)


class ProcessPendingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processed: int
    executed: int
    rejected: int
    errors: int
    enqueued: int


@router.post("/process-pending", response_model=ProcessPendingResponse)
async def process_pending(
    limit: int = Query(default=100, ge=1, le=500),
    quantity: float = Query(default=1.0, gt=0),
    enqueue: bool = Query(default=True),
    container: Container = Depends(get_container),
) -> ProcessPendingResponse:
    if container.trading_halted:
        return ProcessPendingResponse(processed=0, executed=0, rejected=0, errors=0, enqueued=0)
    result = await process_pending_signals(
        limit=limit,
        quantity=quantity,
        process_manager=container.process_manager if enqueue else None,
    )
    return ProcessPendingResponse(**result)


class RequeueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    requeued: bool
    reason: str = Field(default="manual_requeue")


@router.post("/{signal_id}/requeue", response_model=RequeueResponse)
async def requeue(signal_id: str, reason: str = Query(default="manual_requeue")) -> RequeueResponse:
    changed = await requeue_signal(signal_id, reason=reason)
    return RequeueResponse(signal_id=signal_id, requeued=changed, reason=reason)
