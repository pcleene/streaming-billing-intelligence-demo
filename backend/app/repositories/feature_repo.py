"""Feature store repository."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.constants import FEATURES
from app.repositories.base import BaseRepository


class FeatureRepository(BaseRepository):
    COLLECTION_NAME = FEATURES

    async def get(self, customer_id: str) -> dict | None:
        return await self.find_one(
            {"customer_id": customer_id},
            projection={"_id": 0},
        )

    async def upsert(self, customer_id: str, fields: dict) -> int:
        fields["updated_at"] = datetime.now(timezone.utc)
        return await self.update_one(
            {"customer_id": customer_id},
            {"$set": fields},
            upsert=True,
        )

    async def list_freshness(self, *, limit: int = 100) -> list[dict]:
        """For the dashboard 'feature freshness' tile."""
        return await self.find_many(
            {},
            projection={"_id": 0, "customer_id": 1, "updated_at": 1},
            sort=[("updated_at", -1)],
            limit=limit,
        )

    # --- PR-10: lineage / quality writes -----------------------------

    async def update_lineage(
        self,
        customer_id: str,
        *,
        source_txn_at: datetime,
        txn_count: int = 1,
    ) -> int:
        """Atomically bump lineage counters for a feature row.

        Increments ``lineage.source_transactions_count`` by ``txn_count``,
        clamps ``lineage.earliest_source_at`` / ``lineage.latest_source_at``
        with ``$min`` / ``$max`` so out-of-order events don't widen the
        window past the true extremes, and refreshes ``updated_at``.

        Returns ``matched_count`` — the caller is responsible for having
        created the row first (we never upsert here).
        """
        return await self.update_one(
            {"customer_id": customer_id},
            {
                "$inc": {"lineage.source_transactions_count": txn_count},
                "$min": {"lineage.earliest_source_at": source_txn_at},
                "$max": {"lineage.latest_source_at": source_txn_at},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=False,
        )

    async def write_quality(
        self,
        customer_id: str,
        *,
        missing_inputs: list[str] | None = None,
        outlier_flags: list[str] | None = None,
        confidence: float | None = None,
    ) -> int:
        """Patch the quality sub-document.

        Pass-through semantics: only the kwargs the caller provides are
        written. ``confidence`` is clamped to ``[0, 1]``. List arguments
        are ``$set``'d whole (no merge). Returns ``matched_count``.
        """
        set_doc: dict = {}
        if missing_inputs is not None:
            set_doc["quality.missing_inputs"] = list(missing_inputs)
        if outlier_flags is not None:
            set_doc["quality.outlier_flags"] = list(outlier_flags)
        if confidence is not None:
            clamped = max(0.0, min(1.0, float(confidence)))
            set_doc["quality.confidence"] = clamped
        if not set_doc:
            return 0
        set_doc["updated_at"] = datetime.now(timezone.utc)
        return await self.update_one(
            {"customer_id": customer_id},
            {"$set": set_doc},
            upsert=False,
        )

    async def get_with_lineage(self, customer_id: str) -> dict | None:
        """Like :meth:`get` but also returns the ``lineage`` and
        ``quality`` sub-docs (alongside ``customer_id`` and ``updated_at``
        so callers can correlate freshness)."""
        return await self.find_one(
            {"customer_id": customer_id},
            projection={
                "_id": 0,
                "customer_id": 1,
                "lineage": 1,
                "quality": 1,
                "updated_at": 1,
            },
        )
