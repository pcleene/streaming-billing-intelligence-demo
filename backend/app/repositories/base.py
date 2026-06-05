"""Base repository — generic CRUD primitives over PyMongo Async.

Subclasses bind a collection name and surface domain-specific methods.
Repositories are the *only* layer that touches PyMongo collections directly.
"""

from __future__ import annotations

from typing import Any

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.core.logging import get_logger

logger = get_logger(__name__)


class BaseRepository:
    """Thin wrapper around an `AsyncCollection`.

    Subclasses set `COLLECTION_NAME` (str) and add domain-specific methods.
    """

    COLLECTION_NAME: str = ""  # subclass overrides

    def __init__(self, db: AsyncDatabase):
        if not self.COLLECTION_NAME:
            raise ValueError(f"{self.__class__.__name__} must set COLLECTION_NAME")
        self._db = db
        self._coll: AsyncCollection = db[self.COLLECTION_NAME]

    @property
    def collection(self) -> AsyncCollection:
        return self._coll

    # --- Read ---------------------------------------------------------
    async def find_one(self, filter_: dict, projection: dict | None = None) -> dict | None:
        return await self._coll.find_one(filter_, projection)

    async def find_many(
        self,
        filter_: dict,
        *,
        projection: dict | None = None,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict]:
        cursor = self._coll.find(filter_, projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return [doc async for doc in cursor]

    async def count(self, filter_: dict) -> int:
        return await self._coll.count_documents(filter_)

    async def aggregate(self, pipeline: list[dict]) -> list[dict]:
        cursor = await self._coll.aggregate(pipeline)
        return [doc async for doc in cursor]

    # --- Write --------------------------------------------------------
    async def insert_one(self, doc: dict) -> Any:
        result = await self._coll.insert_one(doc)
        return result.inserted_id

    async def insert_many(self, docs: list[dict]) -> list[Any]:
        if not docs:
            return []
        result = await self._coll.insert_many(docs, ordered=False)
        return list(result.inserted_ids)

    async def update_one(self, filter_: dict, update: dict, *, upsert: bool = False) -> int:
        result = await self._coll.update_one(filter_, update, upsert=upsert)
        return result.modified_count

    async def replace_one(self, filter_: dict, replacement: dict, *, upsert: bool = False) -> int:
        result = await self._coll.replace_one(filter_, replacement, upsert=upsert)
        return result.modified_count

    async def delete_one(self, filter_: dict) -> int:
        result = await self._coll.delete_one(filter_)
        return result.deleted_count

    async def delete_many(self, filter_: dict) -> int:
        result = await self._coll.delete_many(filter_)
        return result.deleted_count
