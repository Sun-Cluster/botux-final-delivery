from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Callable, ParamSpec, TypeVar

from app.services.control_plane.types import CpuPoolSnapshot

P = ParamSpec("P")
R = TypeVar("R")


class CpuTaskRunner:
    def __init__(self, *, max_workers: int) -> None:
        self._max_workers = max(1, max_workers)
        self._executor = ProcessPoolExecutor(max_workers=self._max_workers)

    async def run(self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        loop = asyncio.get_running_loop()
        bound = partial(fn, *args, **kwargs)
        return await loop.run_in_executor(self._executor, bound)

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)

    def snapshot(self) -> CpuPoolSnapshot:
        return {"max_workers": self._max_workers}
