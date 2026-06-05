"""Analyst RAG orchestration (AutoEmbed, ADR-032).

Pipeline:
  1. Build the live-case query text via the canonical text builder.
  2. Vector-search `quarantine_cases_history` via the AutoEmbed index —
     Atlas embeds the query string in-cluster with the same Voyage
     model that indexed `embed_source.text`. The application never
     handles a vector.
  3. Pass top-K + the live case to Bedrock Claude under a strict
     structured-output schema. Cite the case_ids the LLM actually used.

If retrieval or Bedrock fail, we surface a degraded response rather
than 500 — the analyst always sees something useful.
"""

from __future__ import annotations

from typing import Any

from app.core.constants import IDX_CASE_HISTORY_AUTOEMBED
from app.core.logging import get_logger
from app.llm.bedrock_client import BedrockClient
from app.llm.prompts import analyst_assist_messages
from app.pipelines.search_builder import SearchPipelineBuilder
from app.repositories.case_history_repo import CaseHistoryRepository
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.services.embedding_service import EmbeddingService

logger = get_logger(__name__)


class RagService:
    def __init__(
        self,
        case_repo: QuarantineCaseRepository,
        history_repo: CaseHistoryRepository,
        bedrock: BedrockClient,
    ) -> None:
        self._cases = case_repo
        self._history = history_repo
        self._bedrock = bedrock

    async def assist(
        self,
        case_id: str,
        *,
        k: int = 5,
        score_threshold: float = 0.30,
        customer: dict[str, Any] | None = None,
        transaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compose an assist payload for `case_id`.

        `customer` / `transaction` are optional rich live docs; when
        the agent's `load_live_state` node has hydrated them, the
        worker passes them through so the Bedrock prompt grounds in
        the live docs. The legacy callers (route handlers, tests) can
        omit them — the prompt then falls back to the case snapshot.
        """
        case = await self._cases.get_by_id(case_id)
        if not case:
            return self._degraded("case_not_found", case_id=case_id)

        # Pure text composition — no network call. AutoEmbed handles the
        # query embedding inside Atlas via `$vectorSearch.query`.
        query_text = EmbeddingService.case_to_embedding_text(case)

        rule_types = [r.get("rule_type") for r in case.get("rules_triggered", []) if r.get("rule_type")]
        meta_filter = {"rules_triggered.rule_type": {"$in": rule_types}} if rule_types else {}

        pipeline = (
            SearchPipelineBuilder()
            .with_vector_search(
                query_text,
                IDX_CASE_HISTORY_AUTOEMBED,
                k=k,
                num_candidates=max(k * 30, 150),
            )
            .with_metadata_filter(meta_filter)
            .with_score_threshold(score_threshold)
            .with_projection({
                "_id": 0, "case_id": 1, "score": 1,
                "rules_triggered": 1, "disposition": 1,
                "analyst_notes": 1, "resolved_at": 1,
                "customer_snapshot": 1, "amount": 1,
                # AutoEmbed surface: the source text is on
                # `embed_source.text`. Project the whole object so
                # downstream code can lift `.text` for previews.
                "embed_source": 1,
            })
            .with_limit(k)
            .build()
        )
        try:
            similars = await self._history.vector_search(pipeline)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag_vector_search_failed", error=str(exc))
            similars = []

        try:
            messages = analyst_assist_messages(
                case, similars, customer=customer, transaction=transaction
            )
            structured = await self._bedrock.invoke_structured(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag_bedrock_failed", error=str(exc))
            return self._degraded("bedrock_failed", case=case, similars=similars)

        return {
            "case_id": case_id,
            "similar_cases": similars,
            "assist": structured,
            "degraded": False,
        }

    def _degraded(self, reason: str, **ctx: Any) -> dict[str, Any]:
        return {
            "case_id": ctx.get("case_id") or (ctx.get("case") or {}).get("case_id"),
            "similar_cases": ctx.get("similars", []),
            "assist": {
                "summary": f"AI assist unavailable: {reason}. Falling back to rule evidence.",
                "likelihood": "unknown",
                "confidence": 0.0,
                "rationale": [],
                "recommended_steps": ["Review rule evidence manually."],
                "references": [],
            },
            "degraded": True,
            "reason": reason,
        }
