"""Composable search pipeline builder (ADR-004 + ADR-032 AutoEmbed).

Used by RAG retrieval, rule testing against history, and analyst case search.
Services compose stages fluently; repositories execute.

AutoEmbed contract: callers pass `query_text` (not a vector). Atlas
embeds the query string in-cluster using the same Voyage model that
indexed `embed_source.text`. The application never sees a vector.

Example:
    pipeline = (
        SearchPipelineBuilder()
        .with_vector_search(query_text, IDX_CASE_HISTORY_AUTOEMBED, k=5)
        .with_metadata_filter({"rules_triggered.rule_type": {"$in": types}})
        .with_customer_context()
        .with_score_threshold(0.75)
        .build()
    )
    results = await case_history_repo.vector_search(pipeline)
"""

from __future__ import annotations

from typing import Self

from app.core.constants import CUSTOMERS, EMBED_SOURCE_PATH


class SearchPipelineBuilder:
    """Fluent aggregation pipeline builder for vector + metadata + lookup."""

    def __init__(self) -> None:
        self._stages: list[dict] = []
        self._has_vector_search = False
        self._has_score_field = False

    # --- Stage builders -----------------------------------------------
    def with_vector_search(
        self,
        query_text: str,
        index_name: str,
        *,
        path: str = EMBED_SOURCE_PATH,
        k: int = 5,
        num_candidates: int | None = None,
        filter_: dict | None = None,
    ) -> Self:
        """$vectorSearch (AutoEmbed) must be the first stage.

        AutoEmbed pushes the query through the same Voyage model that
        embedded `embed_source.text` at index time, so callers pass the
        raw query string (not a pre-computed vector). `path` defaults
        to `EMBED_SOURCE_PATH` because the AutoEmbed index is the only
        vector index in the system today.
        """
        if self._stages:
            raise ValueError("with_vector_search must be the first stage")
        if not query_text:
            raise ValueError("with_vector_search requires a non-empty query_text")
        stage: dict = {
            "$vectorSearch": {
                "index": index_name,
                "path": path,
                # AutoEmbed: pass the raw text; Atlas embeds it server-side.
                "query": query_text,
                "numCandidates": num_candidates or max(k * 20, 100),
                "limit": k,
            }
        }
        if filter_:
            stage["$vectorSearch"]["filter"] = filter_
        self._stages.append(stage)
        # Always project the score so downstream stages can filter.
        self._stages.append({
            "$set": {"score": {"$meta": "vectorSearchScore"}}
        })
        self._has_vector_search = True
        self._has_score_field = True
        return self

    def with_metadata_filter(self, filters: dict) -> Self:
        """Post-filter with $match.

        For best performance with vector search, prefer passing the filter to
        with_vector_search() so the index handles it; this method is for
        post-retrieval narrowing.
        """
        if filters:
            self._stages.append({"$match": filters})
        return self

    def with_customer_context(
        self,
        *,
        local_field: str = "customer_id",
        as_field: str = "customer",
    ) -> Self:
        """$lookup the customer for context. Unwinds preserving missing matches."""
        self._stages.append({
            "$lookup": {
                "from": CUSTOMERS,
                "localField": local_field,
                "foreignField": "customer_id",
                "as": as_field,
            }
        })
        self._stages.append({
            "$unwind": {"path": f"${as_field}", "preserveNullAndEmptyArrays": True}
        })
        return self

    def with_score_threshold(self, min_score: float) -> Self:
        if not self._has_score_field:
            raise ValueError("with_score_threshold requires a vector-search stage first")
        self._stages.append({"$match": {"score": {"$gte": min_score}}})
        return self

    def with_projection(self, fields: dict | list[str]) -> Self:
        if isinstance(fields, list):
            projection = {f: 1 for f in fields}
            projection.setdefault("_id", 0)
        else:
            projection = fields
        self._stages.append({"$project": projection})
        return self

    def with_limit(self, n: int) -> Self:
        self._stages.append({"$limit": n})
        return self

    def with_sort(self, sort: list[tuple[str, int]]) -> Self:
        self._stages.append({"$sort": dict(sort)})
        return self

    # --- Build -------------------------------------------------------
    def build(self) -> list[dict]:
        if not self._stages:
            raise ValueError("Pipeline is empty")
        return list(self._stages)
