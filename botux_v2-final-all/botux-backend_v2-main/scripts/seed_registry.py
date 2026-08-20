from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from app.services.registry.seeder import seed_registry
from config import load_configs
from db.postgres import close_tortoise, init_tortoise
from runtime.logging import configure_logging


async def _run(mode: str) -> None:
    config = load_configs()
    configure_logging(config.log_level)
    await init_tortoise(config)
    try:
        result = await seed_registry(mode=mode)
    finally:
        await close_tortoise()
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed BOTUX bot/strategy registry.")
    parser.add_argument(
        "--mode",
        choices=("missing_only", "repair", "replace"),
        default="repair",
        help="missing_only=create only missing rows, repair=fill missing fields, replace=overwrite canonical rows",
    )
    args = parser.parse_args()
    asyncio.run(_run(mode=args.mode))


if __name__ == "__main__":
    main()
