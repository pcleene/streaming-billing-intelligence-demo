"""Charge codes catalog repository (PR-5).

The catalog is small (tens of entries) and changes infrequently but is
hot-read on every transaction line. Reads route through
`app.services.charge_code_cache` (in-memory singleton, change-stream
invalidated). This repository is the persistence layer that the cache
loads from and that admin tooling writes through.

Public surface:
- `get(code)` → doc or `None`.
- `list_all()` → every code, ordered by `code`.
- `upsert(doc)` → idempotent `$set` keyed on `code`. Returns
  `("inserted", doc)` or `("updated", doc)` so seed scripts can report
  meaningful counts.
- `mark_deprecated(code, *, deprecated_at)` → flip `deprecated` true.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Literal

from app.core.constants import CHARGE_CODES
from app.core.logging import get_logger
from app.repositories.base import BaseRepository

logger = get_logger(__name__)

UpsertOutcome = Literal["inserted", "updated"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChargeCodeRepository(BaseRepository):
    COLLECTION_NAME = CHARGE_CODES

    async def get(self, code: str) -> dict | None:
        return await self.find_one({"code": code})

    async def list_all(self) -> list[dict]:
        return await self.find_many({}, sort=[("code", 1)])

    async def upsert(self, doc: dict) -> tuple[UpsertOutcome, dict]:
        """Insert-or-update a charge code keyed on `code`.

        The caller owns `code`, `name`, `revenue_category`, `gl_account`,
        `tax`, `applies_to`, `approval`, `effective_period`, etc. We
        stamp `_updated_at` on every write, and `_created_at` only on
        the insert branch via `$setOnInsert`.
        """
        if "code" not in doc:
            raise ValueError("charge code upsert requires `code`")
        payload = copy.deepcopy(doc)
        payload["_updated_at"] = _utcnow()
        result = await self._coll.update_one(
            {"code": payload["code"]},
            {
                "$set": payload,
                "$setOnInsert": {"_created_at": _utcnow()},
            },
            upsert=True,
        )
        # PyMongo update_one returns matched_count; in the fake we
        # surface `upserted_id` on the insert branch.
        outcome: UpsertOutcome = (
            "inserted" if getattr(result, "upserted_id", None) else "updated"
        )
        stored = await self.get(payload["code"])
        return outcome, stored or payload

    async def mark_deprecated(self, code: str, *, deprecated_at: datetime | None = None) -> int:
        return await self.update_one(
            {"code": code},
            {"$set": {
                "deprecated": True,
                "deprecated_at": deprecated_at or _utcnow(),
                "_updated_at": _utcnow(),
            }},
        )
