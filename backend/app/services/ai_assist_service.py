"""AI-assist generation service (Phase B.4).

Wraps the existing RAG flow (`RagService`) but persists a normalized
`AiAssist` payload onto `quarantine_cases.ai_assist`. Idempotent: calling
the route twice within `freshness_seconds` returns the existing
projection instead of re-querying Bedrock.

Schema is enforced server-side via `AiAssist` Pydantic model + a
`$jsonSchema` validator on the case collection (set in
`scripts/setup_validators.py`).

PR-8 additions:
- After generating `ai_assist`, denormalises the top-3 similar cases
  onto the case doc as `similar_cases_preview` (matches the
  `SimilarCasePreview` schema).
- Atomically `$inc`'s `used_in_rag_count` on each retrieved historical
  case via `CaseHistoryRepository.record_rag_usage(was_useful=False)`
  at generation time.
- Adds `register_disposition_feedback` so the worker can flip the
  positive/negative counter on each previewed history case once an
  analyst dispositions the live case.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.core.constants import IDX_CASE_HISTORY_AUTOEMBED
from app.core.errors import CaseNotFound
from app.core.feature_flags import flags
from app.core.logging import get_logger
from app.repositories.case_history_repo import CaseHistoryRepository
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.schemas.quarantine import (
    AiAssist,
    AiAssistCitation,
    AiAssistRetrieval,
)
from app.services.rag_service import RagService

if TYPE_CHECKING:
    # Avoid pulling LangGraph at import time when the agentic flag is
    # off (keeps the FastAPI / worker startup cheap).
    from app.services.assist_agent import AssistAgent

logger = get_logger(__name__)


# How long an ai_assist projection is considered fresh enough to reuse on
# repeat POSTs. Lets analysts hit the button twice without burning quota.
DEFAULT_FRESHNESS_SECONDS = 300

# How many similar cases to denormalise onto the live case doc.
SIMILAR_CASES_PREVIEW_LIMIT = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> datetime:
    """Normalise an arbitrary ts-ish value to a tz-aware datetime.

    Used when projecting `resolved_at` from a similar-case dict onto the
    `SimilarCasePreview.resolved_at` field which is required.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _utcnow()


def _coerce_bullets(value: Any, *, fallback: str) -> list[str]:
    """Normalise an LLM bullet field to a `list[str]`.

    Bedrock occasionally returns ``rationale`` / ``recommended_steps`` as
    a single string (numbered paragraphs) instead of a JSON array. The
    previous implementation called ``list(value)`` directly, which
    silently exploded the string into a list of single characters —
    the persisted document then carried 625 one-char entries, the UI
    rendered the noisy fallback, and analyst trust evaporated.

    Behaviour:
      * ``list`` of strings → stripped + de-empty-filtered, fallback if
        nothing remains.
      * ``str`` (single bullet or paragraph) → wrapped in a one-element
        list. Newline-delimited / bullet-prefixed strings are split into
        individual lines so the LLM's "1. foo\\n2. bar" output still
        renders as a numbered list, not one giant blob.
      * ``None`` / falsy → ``[fallback]`` so the Pydantic validator
        (``min_length=1``) stays happy.
    """
    if value is None or value == "":
        return [fallback]
    if isinstance(value, list):
        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    cleaned.append(stripped)
            elif item is not None:
                cleaned.append(str(item))
        return cleaned or [fallback]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return [fallback]
        # Split on newlines so numbered prose ("1. foo\n2. bar") becomes
        # discrete bullets. Strip common list prefixes so the UI's own
        # `<ol>` numbering doesn't double up.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        cleaned = []
        for ln in lines:
            for prefix in ("- ", "* ", "• "):
                if ln.startswith(prefix):
                    ln = ln[len(prefix):].lstrip()
                    break
            # Strip "1." / "1)" numbering at the start.
            if len(ln) > 2 and ln[0].isdigit():
                # find first non-digit
                i = 0
                while i < len(ln) and ln[i].isdigit():
                    i += 1
                if i < len(ln) and ln[i] in ".)" and i + 1 < len(ln) and ln[i + 1] == " ":
                    ln = ln[i + 2:].lstrip()
            if ln:
                cleaned.append(ln)
        return cleaned or [text]
    # Anything else (dict, number, etc) → stringify, keep validator happy.
    return [str(value)]


def _build_similar_preview(similar: dict) -> dict:
    """Project a single RagService.assist `similar_cases` entry to the
    `similar_cases_preview` denorm shape.

    The RAG projection (see `rag_service.py`) yields:
      case_id, score, rules_triggered, disposition, analyst_notes,
      resolved_at, customer_snapshot, amount, embed_source.

    We map `score` → `relevance` (clamped 0..1) and synthesise a short
    `summary` from `embed_source.text`/`analyst_notes` so the analyst
    tile has something to show without re-fetching the history doc.
    """
    relevance = similar.get("score")
    try:
        relevance_f = float(relevance) if relevance is not None else 0.0
    except (TypeError, ValueError):
        relevance_f = 0.0
    relevance_f = max(0.0, min(1.0, relevance_f))

    embed_source = similar.get("embed_source") or {}
    summary = (
        (embed_source.get("text") if isinstance(embed_source, dict) else None)
        or similar.get("analyst_notes")
        or ""
    )
    if isinstance(summary, str) and len(summary) > 240:
        summary = summary[:237] + "..."

    return {
        "case_id": str(similar.get("case_id") or ""),
        "relevance": relevance_f,
        "disposition": str(similar.get("disposition") or "unknown"),
        "resolved_at": _coerce_datetime(similar.get("resolved_at")),
        "summary": summary,
    }


class AiAssistService:
    def __init__(
        self,
        case_repo: QuarantineCaseRepository,
        rag: RagService,
        freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
        history_repo: CaseHistoryRepository | None = None,
        agent: "AssistAgent | None" = None,
    ) -> None:
        self._cases = case_repo
        self._rag = rag
        self._freshness_seconds = freshness_seconds
        # `history_repo` is optional so existing callers (and tests that
        # don't exercise the RAG-feedback path) keep working untouched.
        self._history = history_repo
        # PR-AG: optional LangGraph-backed agent. When provided AND
        # `flags.AI_ASSIST_AGENTIC` is on, `generate()` routes through
        # `agent.run(case_id=...)` instead of the linear RAG path.
        self._agent = agent

    async def generate(
        self,
        case_id: str,
        *,
        k: int = 5,
        threshold: float = 0.30,
        force: bool = False,
    ) -> dict[str, Any]:
        case = await self._cases.get_by_id(case_id)
        if not case:
            raise CaseNotFound(case_id)

        existing = case.get("ai_assist")
        if not force and self._is_fresh(existing):
            return {"case_id": case_id, "ai_assist": existing, "cached": True}

        # PR-AG: route through the LangGraph agent when wired + flag-on.
        if self._agent is not None and flags.AI_ASSIST_AGENTIC:
            return await self._generate_agentic(
                case_id, k=k, threshold=threshold
            )
        return await self._generate_linear(case_id, k=k, threshold=threshold)

    async def _generate_linear(
        self,
        case_id: str,
        *,
        k: int,
        threshold: float,
    ) -> dict[str, Any]:
        """Existing PR-8 path: RAG → materialise → persist + denorm."""
        rag = await self._rag.assist(case_id, k=k, score_threshold=threshold)
        assist_payload = rag.get("assist") or {}
        similars = rag.get("similar_cases") or []

        ai_assist = self._materialise(
            assist_payload=assist_payload,
            similars=similars,
            k=k,
            threshold=threshold,
            degraded=bool(rag.get("degraded")),
            degraded_reason=rag.get("reason"),
        )

        # PR-8: denormalise the top-N similar cases onto the live case
        # doc so the analyst UI can render previews without joining
        # against the history corpus.
        previews = [
            _build_similar_preview(s)
            for s in similars[:SIMILAR_CASES_PREVIEW_LIMIT]
            if s.get("case_id")
        ]

        await self._cases.update_one(
            {"case_id": case_id},
            {
                "$set": {
                    "ai_assist": ai_assist.model_dump(mode="json"),
                    "similar_cases_preview": [
                        {**p, "resolved_at": p["resolved_at"].isoformat()}
                        for p in previews
                    ],
                }
            },
        )

        # PR-8: bump `used_in_rag_count` on every retrieved history doc
        # (not just the top-3 preview) so corpus stewardship can see what
        # the LLM actually saw, even if the UI only shows three.
        # `was_useful=False` because we don't yet know the analyst's
        # verdict — `register_disposition_feedback` flips the proper
        # positive/negative counter once disposition lands.
        if self._history is not None:
            for s in similars:
                hcid = s.get("case_id")
                if not hcid:
                    continue
                try:
                    await self._history.record_rag_usage(hcid, was_useful=False)
                except Exception as exc:  # noqa: BLE001
                    # Non-fatal: corpus telemetry should never break
                    # the analyst-facing assist flow.
                    logger.warning(
                        "rag_usage_record_failed",
                        history_case_id=hcid,
                        error=str(exc),
                    )

        return {"case_id": case_id, "ai_assist": ai_assist.model_dump(mode="json"), "cached": False}

    async def _generate_agentic(
        self,
        case_id: str,
        *,
        k: int,
        threshold: float,
    ) -> dict[str, Any]:
        """PR-AG: LangGraph agent path.

        Drives `AssistAgent.run(case_id)` and projects the final state's
        `synthesis` payload onto the same `AiAssist` shape the linear
        path produces. The graph's `trace` is stored as
        `ai_assist.agent_trace` so analysts can inspect which nodes ran.

        The agent already orchestrates analytics + retrieval, so we
        translate `state["retrieval"]` into the `similars` slot the
        materialiser expects. Errors in any single node land in
        `state["errors"]` which feeds `degraded` / `degraded_reason`.
        """
        state = await self._agent.run(case_id=case_id)  # type: ignore[union-attr]
        synthesis = state.get("synthesis") or {}
        similars = state.get("retrieval") or []
        errors = state.get("errors") or []
        trace = state.get("trace") or []

        ai_assist = self._materialise(
            assist_payload=synthesis,
            similars=similars,
            k=k,
            threshold=threshold,
            degraded=bool(synthesis.get("degraded") or errors),
            degraded_reason=(
                synthesis.get("degraded_reason")
                or ("; ".join(errors) if errors else None)
            ),
        )

        ai_assist_doc: dict[str, Any] = ai_assist.model_dump(mode="json")
        # Persist the per-node trace alongside the assist so the UI can
        # render the agent's reasoning path without re-running the graph.
        ai_assist_doc["agent_trace"] = list(trace)

        previews = [
            _build_similar_preview(s)
            for s in similars[:SIMILAR_CASES_PREVIEW_LIMIT]
            if isinstance(s, dict) and s.get("case_id")
        ]

        await self._cases.update_one(
            {"case_id": case_id},
            {
                "$set": {
                    "ai_assist": ai_assist_doc,
                    "similar_cases_preview": [
                        {**p, "resolved_at": p["resolved_at"].isoformat()}
                        for p in previews
                    ],
                }
            },
        )

        # Mirror linear-path corpus telemetry so the agentic rollout
        # doesn't silently drop `used_in_rag_count` increments.
        if self._history is not None:
            for s in similars:
                if not isinstance(s, dict):
                    continue
                hcid = s.get("case_id")
                if not hcid:
                    continue
                try:
                    await self._history.record_rag_usage(hcid, was_useful=False)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "rag_usage_record_failed",
                        history_case_id=hcid,
                        error=str(exc),
                    )

        return {
            "case_id": case_id,
            "ai_assist": ai_assist_doc,
            "cached": False,
        }

    async def register_disposition_feedback(
        self, *, case_id: str, was_useful: bool
    ) -> None:
        """Called when an analyst dispositions a case.

        Walks the case's `similar_cases_preview` and increments the
        positive (was_useful=True) or negative (was_useful=False)
        counter on `rag_relevance_feedback` of each previewed
        historical case. No-op when `similar_cases_preview` is missing
        or empty, or when no `history_repo` was wired in.
        """
        if self._history is None:
            return
        case = await self._cases.get_by_id(case_id)
        if not case:
            return
        previews = case.get("similar_cases_preview") or []
        for p in previews:
            hcid = p.get("case_id") if isinstance(p, dict) else None
            if not hcid:
                continue
            try:
                await self._history.record_rag_usage(hcid, was_useful=was_useful)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rag_feedback_record_failed",
                    history_case_id=hcid,
                    error=str(exc),
                )

    def _is_fresh(self, existing: dict | None) -> bool:
        if not existing:
            return False
        ts = existing.get("generated_at")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return False
        if not isinstance(ts, datetime):
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_utcnow() - ts) < timedelta(seconds=self._freshness_seconds)

    def _materialise(
        self,
        *,
        assist_payload: dict,
        similars: list[dict],
        k: int,
        threshold: float,
        degraded: bool,
        degraded_reason: str | None,
    ) -> AiAssist:
        references = [
            AiAssistCitation(
                case_id=str(r.get("case_id")),
                disposition=str(r.get("disposition", "unknown")),
                score=r.get("score"),
                why_relevant=r.get("why_relevant"),
            )
            for r in (assist_payload.get("references") or [])
            if r.get("case_id")
        ]
        retrieval = AiAssistRetrieval(
            index_name=IDX_CASE_HISTORY_AUTOEMBED,
            k=k,
            score_threshold=threshold,
            retrieved_case_ids=[s.get("case_id") for s in similars if s.get("case_id")],
            # AutoEmbed: the embedding model lives in Atlas project settings
            # (not in app config). Surface a fixed marker so dashboards can
            # group runs by provenance without leaking infra config.
            embedding_model="atlas-autoembed",
        )

        # Tolerate degraded payloads where rationale/recommended_steps are
        # empty — keep min_length=1 happy by injecting a fallback.
        rationale = _coerce_bullets(
            assist_payload.get("rationale"),
            fallback="No rationale generated.",
        )
        recommended = _coerce_bullets(
            assist_payload.get("recommended_steps"),
            fallback="Review rule evidence manually.",
        )

        likelihood = assist_payload.get("likelihood", "needs_more_info")
        if likelihood not in ("true_positive", "false_positive", "needs_more_info"):
            likelihood = "needs_more_info"
        return AiAssist(
            summary=str(assist_payload.get("summary", "AI assist unavailable.")),
            likelihood=likelihood,
            confidence=float(assist_payload.get("confidence", 0.0) or 0.0),
            rationale=rationale,
            recommended_steps=recommended,
            references=references,
            retrieval=retrieval,
            model=settings.bedrock_model_id,
            degraded=degraded,
            degraded_reason=degraded_reason,
        )
