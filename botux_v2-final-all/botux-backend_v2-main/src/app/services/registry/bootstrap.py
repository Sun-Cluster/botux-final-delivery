from __future__ import annotations

from app.services.registry.seeder import seed_registry


async def bootstrap_canonical_registry() -> dict[str, object]:
    return await seed_registry(mode="repair")
