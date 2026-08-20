from __future__ import annotations

from loguru import logger
from tortoise import Tortoise

from app.services.registry.bootstrap import bootstrap_canonical_registry
from app.services.runtime_config.service import RuntimeConfigService
from app.services.runtime_config.bootstrap import bootstrap_runtime_controls
from db.postgres import close_tortoise, init_tortoise
from infra.scheduler.runner import start_scheduler
from runtime.container import Container


async def startup(container: Container) -> None:
    logger.info(
        "startup begin env={} skip_registry_bootstrap={}",
        container.config.env,
        container.config.skip_registry_bootstrap,
    )
    if not Tortoise.is_inited():
        await init_tortoise(container.config)
        container.db_context_owned = True
        container.db_context_ready = True
        logger.info("db context initialized")
    else:
        container.db_context_owned = False
        container.db_context_ready = True
        logger.info("db context already initialized")
    runtime_control_bootstrap = await bootstrap_runtime_controls()
    logger.info(
        "runtime control bootstrap completed seeded={} total={}",
        runtime_control_bootstrap["seeded"],
        runtime_control_bootstrap["config_count"],
    )
    broker_default = await RuntimeConfigService().resolve("broker.default")
    container.broker_router.set_default_broker(str(broker_default.value or "alpaca"))
    container.broker = container.broker_router.default_broker
    logger.info("runtime broker default applied broker={}", container.broker_router.default_broker_name)
    if not container.config.skip_registry_bootstrap:
        await bootstrap_canonical_registry()
        logger.info("registry bootstrap completed")
    else:
        logger.warning(
            "registry bootstrap skipped because BOTUX_SKIP_REGISTRY_BOOTSTRAP=1 "
            "(or deprecated BOTUX_SKIP_DB_INIT=1)"
        )
    container.scheduler = await start_scheduler(container)
    if container.scheduler is None:
        logger.info("scheduler not started (no jobs registered)")
    else:
        logger.info("scheduler started with job_count={}", container.scheduler.snapshot()["job_count"])
    if container.process_manager is not None:
        await container.process_manager.start()
        logger.info("process manager started")
    logger.info("startup completed")


async def shutdown(container: Container) -> None:
    logger.info("shutdown begin")
    if container.scheduler is not None:
        await container.scheduler.stop()
        container.scheduler = None
        logger.info("scheduler stopped")
    if container.process_manager is not None:
        await container.process_manager.stop()
        logger.info("process manager stopped")
    if container.db_context_owned:
        await close_tortoise()
        logger.info("db connections closed")
    container.db_context_ready = False
    container.db_context_owned = False
    logger.info("shutdown completed")
