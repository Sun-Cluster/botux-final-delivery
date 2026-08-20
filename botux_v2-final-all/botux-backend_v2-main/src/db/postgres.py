from loguru import logger
from tortoise import Tortoise
from tortoise.connection import get_connection

from config import AppConfig
from db.tortoise_config import build_tortoise_config

DB_RECONNECT_INTERVAL_SECONDS = 5


async def init_tortoise(settings: AppConfig, retry = 10) -> None:
    await Tortoise.init(
        config=build_tortoise_config(settings),
        _enable_global_fallback=True,
    )

    await get_connection("default").create_connection(with_db=True)
    logger.info("db init completed")
        
    if settings.db_auto_schema:
        logger.info("db auto schema generation start")
        await Tortoise.generate_schemas(safe=True)
        logger.info("db auto schema generation completed")


async def close_tortoise() -> None:
    logger.info("db close connections start")
    await Tortoise.close_connections()
    logger.info("db close connections completed")
