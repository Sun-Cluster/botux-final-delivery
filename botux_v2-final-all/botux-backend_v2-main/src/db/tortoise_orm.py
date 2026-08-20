from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DB_URI = os.getenv("BOTUX_DB_URI", "").strip()
if not DB_URI:
    raise RuntimeError("Missing required env var: BOTUX_DB_URI")

TORTOISE_ORM = {
    "connections": {"default": DB_URI},
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
