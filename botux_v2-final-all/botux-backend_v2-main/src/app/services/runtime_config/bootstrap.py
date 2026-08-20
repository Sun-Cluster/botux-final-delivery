from __future__ import annotations

import os
from datetime import datetime, timezone

from app.services.runtime_config.service import RUNTIME_CONFIG_DEFINITIONS, coerce_runtime_config_value
from db.repositories.system_configs_repo import SystemConfigsRepository
from db.uow import UnitOfWork


async def bootstrap_runtime_controls() -> dict[str, object]:
    async with UnitOfWork() as uow:
        repo = SystemConfigsRepository(connection=uow.connection)
        seeded = 0
        for key, definition in RUNTIME_CONFIG_DEFINITIONS.items():
            existing = await repo.get_by_key(key)
            if existing is not None:
                continue

            val = definition.default

            await repo.upsert(
                key=key,
                value=val,
                value_type=definition.value_type,
                scope=definition.scope,
                description=definition.description,
                updated_by="bootstrap:default",
            )
            seeded += 1
    return {
        "seeded": seeded,
        "config_count": len(RUNTIME_CONFIG_DEFINITIONS),
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }
