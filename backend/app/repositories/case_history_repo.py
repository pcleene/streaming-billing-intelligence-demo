"""Quarantine case history repository — the RAG corpus.

This is the only repository that exposes vector-search aggregation directly.
The pipeline is built via SearchPipelineBuilder; this repo just executes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.constants import QUARANTINE_CASES_HISTORY
from app.repositories.base import BaseRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CaseHistoryRepository(BaseRepository):
    COLLECTION_NAME = QUARANTINE_CASES_HISTORY

    async def get_by_id(self, case_id: str) -> dict | None:
        # AutoEmbed (ADR-032) stores the vector inside Atlas — there is no
        # `embedding` field on the document — so no projection is needed.
        return await self.find_one({"case_id": case_id})

    async def list_paged(self, *, skip: int = 0, limit: int = 50) -> list[dict]:
        return await self.find_many(
            {},
            sort=[("resolved_at", -1)],
            skip=skip,
            limit=limit,
        )

    async def upsert_resolved(self, doc: dict) -> str:
        """Append a freshly-resolved case to the corpus, idempotent on case_id.

        Re-archiving the same case (e.g. a disposition is re-applied or the
        worker is restarted mid-archive) replaces the existing history row
        rather than duplicating it. The unique `case_id_unique` index on
        quarantine_cases_history backs the idempotency guarantee.

        Caller is expected to have set `embed_source.text` on the doc (see
        `QuarantineService._archive_to_history` for the canonical write
        path). Atlas Auto Embedding indexes that string into the vector
        invisibly (ADR-032).
        """
        case_id = doc["case_id"]
        await self.replace_one({"case_id": case_id}, doc, upsert=True)
        return case_id

    async def vector_search(self, pipeline: list[dict]) -> list[dict]:
        """Execute a pre-built vector-search pipeline (built by SearchPipelineBuilder)."""
        return await self.aggregate(pipeline)

    async def record_rag_usage(self, case_id: str, *, was_useful: bool) -> int:
        """Record that this historical case was used in a RAG retrieval.

        Bumps `used_in_rag_count`, sets `last_used_in_rag_at = now()`, and
        increments `rag_relevance_feedback.positive` (when `was_useful=True`)
        or `rag_relevance_feedback.negative` (when `was_useful=False`).

        The schema for `rag_relevance_feedback` is the `RagFeedback`
        sub-document defined in `app/schemas/quarantine.py` (positive /
        negative / neutral int counters), so we use a `$inc` on the dotted
        sub-key rather than a `$push` of an event.

        Returns matched_count (1 if the row exists and was updated, 0 if
        no history row matched the case_id — this is a no-op rather than
        an error, since the worker may invoke us before the archive row
        has been written by another agent).
        """
        feedback_key = (
            "rag_relevance_feedback.positive"
            if was_useful
            else "rag_relevance_feedback.negative"
        )
        result = await self._coll.update_one(
            {"case_id": case_id},
            {
                "$inc": {
                    "used_in_rag_count": 1,
                    feedback_key: 1,
                },
                "$set": {"last_used_in_rag_at": _utcnow()},
            },
        )
        return result.matched_count
