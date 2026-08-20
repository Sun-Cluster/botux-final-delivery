from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_container
from app.services.reconcile.service import ReconcileService
from runtime.container import Container

router = APIRouter(prefix="/reconcile", tags=["reconcile"])


@router.post("/run")
async def run_reconcile(container: Container = Depends(get_container)) -> dict[str, object]:
    service = ReconcileService(broker=container.broker)
    try:
        report = await service.run()
    except Exception as exc:
        return {"error": str(exc)[:200], "status": "failed"}
    container.last_reconcile_report = report
    timestamp = report.get("timestamp")
    if isinstance(timestamp, str):
        container.last_reconcile_run_at = timestamp
    return report


@router.get("/status")
async def reconcile_status(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.last_reconcile_report is None:
        return {
            "status": "never_run",
            "last_run": None,
            "has_report": False,
            "note": "Reconciliation has not run in this process",
        }
    issues = container.last_reconcile_report.get("issues", [])
    issue_count = len(issues) if isinstance(issues, list) else 0
    return {
        "status": "ok" if issue_count == 0 else "warn",
        "last_run": container.last_reconcile_run_at,
        "has_report": True,
        "issues": issue_count,
    }


@router.get("/report")
async def reconcile_report(container: Container = Depends(get_container)) -> dict[str, object]:
    if container.last_reconcile_report is None:
        return {"error": "No report available. Run POST /reconcile/run first."}
    return container.last_reconcile_report
