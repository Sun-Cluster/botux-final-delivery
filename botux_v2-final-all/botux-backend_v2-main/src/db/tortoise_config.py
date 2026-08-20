from __future__ import annotations

from config import AppConfig


def build_tortoise_config(settings: AppConfig) -> dict:
    return {
        "connections": {"default": settings.db_uri},
        "apps": {
            "models": {
                "models": ["src.db.models"],
                "default_connection": "default",
                "migrations": "src.db.migrations",
            }
        },
        "use_tz": True,
        "timezone": "UTC",
    }
