from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from loguru import logger
from tortoise.backends.base.client import BaseDBAsyncClient, TransactionContext
from tortoise.transactions import in_transaction


class _CommitRollbackConnection(Protocol):
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWork(AbstractAsyncContextManager["UnitOfWork"]):
    def __init__(self) -> None:
        self._txn_ctx: TransactionContext | None = None
        self.connection: BaseDBAsyncClient | None = None

    async def __aenter__(self) -> "UnitOfWork":
        logger.debug("uow transaction enter")
        self._txn_ctx = in_transaction()
        self.connection = await self._txn_ctx.__aenter__()
        return self

    async def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        logger.debug("uow transaction commit")
        await cast(_CommitRollbackConnection, self.connection).commit()

    async def rollback(self) -> None:
        if self.connection is None:
            return
        logger.warning("uow transaction rollback")
        await cast(_CommitRollbackConnection, self.connection).rollback()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._txn_ctx is None:
            return
        if exc is None:
            logger.debug("uow transaction exit")
        else:
            logger.error("uow transaction exit with error: {}", str(exc)[:500])
        await self._txn_ctx.__aexit__(exc_type, exc, tb)
        self.connection = None
        self._txn_ctx = None
