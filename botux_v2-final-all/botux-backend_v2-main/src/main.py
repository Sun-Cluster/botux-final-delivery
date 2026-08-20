from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.bot_registry import router as bot_registry_router
from api.routers.api_extra import router as api_extra_router
from api.routers.autopilot import router as autopilot_router
from api.routers.control_plane_compat import router as control_plane_compat_router
from api.routers.core_api import router as core_api_router
from api.routers.health import router as health_router
from api.routers.intel_compat import router as intel_compat_router
from api.routers.portfolio import router as portfolio_router
from api.routers.reconcile import router as reconcile_router
from api.routers.risk_compat import router as risk_compat_router
from api.routers.runtime import router as runtime_router
from api.routers.settings import router as settings_router
from api.routers.signals import router as signals_router
from runtime.bootstrap import shutdown, startup
from runtime.container import build_container
from runtime.logging import configure_logging

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.container = build_container()
    await startup(app.state.container)
    try:
        yield
    finally:
        await shutdown(app.state.container)


app = FastAPI(title="BOTUX Refactor API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(runtime_router)
app.include_router(settings_router)
app.include_router(signals_router)
app.include_router(portfolio_router)
app.include_router(reconcile_router)
app.include_router(core_api_router)
app.include_router(risk_compat_router)
app.include_router(bot_registry_router)
app.include_router(autopilot_router)
app.include_router(intel_compat_router)
app.include_router(control_plane_compat_router)
app.include_router(api_extra_router)
