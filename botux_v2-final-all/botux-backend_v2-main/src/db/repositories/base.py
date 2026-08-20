from __future__ import annotations

from typing import TypeVar

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.models import Model
from tortoise.queryset import QuerySet

TModel = TypeVar("TModel", bound=Model)


class RepositoryBase:
    def __init__(self, connection: BaseDBAsyncClient | None) -> None:
        self._connection = connection

    def _query(self, queryset: QuerySet[TModel]) -> QuerySet[TModel]:
        if self._connection is None:
            return queryset
        return queryset.using_db(self._connection)

    async def _save(self, model: Model) -> None:
        if self._connection is None:
            await model.save()
            return
        await model.save(using_db=self._connection)
