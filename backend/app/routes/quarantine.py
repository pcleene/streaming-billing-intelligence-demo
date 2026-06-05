"""Quarantine cases listing + disposition (Pillar 2 / Pillar 3 surface)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.constants import QUARANTINE_CASES_HISTORY
from app.core.errors import CaseNotFound
from app.deps import DBDep
from app.llm.bedrock_client import BedrockClient
from app.repositories.case_history_repo import CaseHistoryRepository
from app.repositories.quarantine_case_repo import (
    EMBED_OPEN_STATUSES,
    QuarantineCaseRepository,
)
from app.services.agent_observability_service import AgentObservabilityService
from app.services.ai_assist_service import AiAssistService
from app.services.quarantine_service import QuarantineService
from app.services.rag_service import RagService

router = APIRouter(prefix="/api/quarantine", tags=["quarantine"])

# Cap for the batch ai-assist endpoint. Enforced at the route layer (not
# the service) so non-HTTP callers (e.g. workers replaying a backlog)
# stay free to fan out larger batches when appropriate.
BATCH_AI_ASSIST_MAX_IDS = 25


class DispositionPayload(BaseModel):
    disposition: str = Field(..., pattern=r"^(true_positive|false_positive|escalate|needs_more_info)$")
    analyst_id: str
    analyst_notes: str = ""


class BatchAiAssistPayload(BaseModel):
    """Body for `POST /api/quarantine/cases/batch-ai-assist`.

    `case_ids` is bounded at 25 so a single call can't burn an entire
    Bedrock per-minute quota. `force` mirrors the single-case route
    flag — set true to bypass the freshness window.
    """
    case_ids: list[str] = Field(
        ..., min_length=1, max_length=BATCH_AI_ASSIST_MAX_IDS
    )
    force: bool = False


def _quarantine_service(db: DBDep) -> QuarantineService:
    # AutoEmbed (ADR-032): no app-side embedding client. The archive
    # write path composes `embed_source.text` from pure helpers and
    # Atlas embeds it invisibly.
    return QuarantineService(
        QuarantineCaseRepository(db),
        CaseHistoryRepository(db),
    )


def _rag_service(db: DBDep) -> RagService:
    # AutoEmbed: RagService passes raw query text to `$vectorSearch.query`;
    # no Voyage SDK at the application boundary.
    return RagService(
        QuarantineCaseRepository(db),
        CaseHistoryRepository(db),
        BedrockClient(),
    )


def _ai_assist_service(db: DBDep) -> AiAssistService:
    # PR-AG: hand AssistAgent to the service when the agentic flag is
    # on so explicit POST /ai-assist calls exercise the LangGraph path
    # consistent with the case_lifecycle worker. Imports are local so
    # we only pay LangGraph + repo wiring when the flag is on.
    from app.core.feature_flags import flags

    case_repo = QuarantineCaseRepository(db)
    history_repo = CaseHistoryRepository(db)
    agent = None
    if flags.AI_ASSIST_AGENTIC:
        from app.workers.assist_agent_worker import _build_agent
        agent = _build_agent(db)
    return AiAssistService(
        case_repo,
        _rag_service(db),
        history_repo=history_repo,
        agent=agent,
    )


def _agent_observability_service(db: DBDep) -> AgentObservabilityService:
    """Compose the read-side trace/batch service from the same primitives
    used elsewhere in this router. Keeps the dep-injection surface
    consistent with `_ai_assist_service` above so test overrides only
    have to monkey-patch one provider.
    """
    return AgentObservabilityService(
        QuarantineCaseRepository(db),
        _ai_assist_service(db),
    )


@router.get("/cases")
async def list_cases(
    status: str | None = None,
    severity: str | None = None,
    rule_type: str | None = None,
    agent_reviewed: bool | None = Query(
        None,
        description="When true, only cases that have been driven through "
        "the agentic AI assist workflow (i.e. ai_assist.agent_trace is a "
        "non-empty array). When false, the inverse. Omitted = no filter.",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    svc: QuarantineService = Depends(_quarantine_service),
) -> dict:
    return await svc.list_cases(
        status=status,
        severity=severity,
        rule_type=rule_type,
        agent_reviewed=agent_reviewed,
        skip=skip,
        limit=limit,
    )


@router.get("/cases/{case_id}")
async def get_case(case_id: str, svc: QuarantineService = Depends(_quarantine_service)) -> dict:
    return await svc.get_case(case_id)


def _project_related_case_row(case: dict) -> dict:
    """Minimal projection for the related-cases panel.

    Pulls the first rule's `rule_name`/`rule_type` as the headline plus
    a denormalised `amount` / `transaction_id` so the analyst can scan
    each row without opening it.
    """
    rules = case.get("rules_triggered") or []
    headline_rule = rules[0] if rules and isinstance(rules[0], dict) else {}
    txn_summary = case.get("transaction_summary") or {}
    amount = case.get("amount")
    if amount is None and isinstance(txn_summary, dict):
        amount = txn_summary.get("total_myr")
    transaction_id = case.get("transaction_id")
    if transaction_id is None and isinstance(txn_summary, dict):
        transaction_id = txn_summary.get("transaction_id")
    return {
        "case_id": case.get("case_id"),
        "rule_name": headline_rule.get("rule_name"),
        "rule_type": headline_rule.get("rule_type"),
        "severity": case.get("severity"),
        "status": case.get("status"),
        "disposition": case.get("disposition"),
        "created_at": case.get("created_at"),
        "resolved_at": case.get("resolved_at"),
        "amount": amount,
        "transaction_id": transaction_id,
    }


@router.get("/cases/{case_id}/related-customer")
async def related_customer_cases(
    case_id: str,
    db: DBDep,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """Open + historical cases for the same customer as `case_id`.

    Returns ``{open: [...], history: [...]}`` with a small projection
    suitable for a sidebar list. Excludes the current case. 404 when
    the case_id is unknown.
    """
    case_repo = QuarantineCaseRepository(db)
    case = await case_repo.get_by_id(case_id)
    if case is None:
        raise CaseNotFound(case_id)
    customer_id = case.get("customer_id")
    if not customer_id:
        return {"open": [], "history": []}

    open_rows = await case_repo.find_many(
        {
            "customer_id": customer_id,
            "case_id": {"$ne": case_id},
            "status": {"$in": list(EMBED_OPEN_STATUSES)},
        },
        sort=[("created_at", -1)],
        limit=limit,
    )
    # quarantine_cases_history has no dedicated repo with a list-by-
    # customer helper; query the collection directly for the small
    # projection we need here.
    history_cursor = db[QUARANTINE_CASES_HISTORY].find(
        {"customer_id": customer_id, "case_id": {"$ne": case_id}}
    ).sort([("resolved_at", -1)]).limit(limit)
    history_rows = [doc async for doc in history_cursor]

    return {
        "open": [_project_related_case_row(c) for c in open_rows],
        "history": [_project_related_case_row(c) for c in history_rows],
    }


@router.post("/cases/{case_id}/disposition")
async def disposition(
    case_id: str,
    payload: DispositionPayload,
    svc: QuarantineService = Depends(_quarantine_service),
) -> dict:
    return await svc.disposition(
        case_id,
        disposition=payload.disposition,
        analyst_id=payload.analyst_id,
        analyst_notes=payload.analyst_notes,
    )


@router.post("/cases/{case_id}/assist")
async def assist(
    case_id: str,
    k: int = Query(5, ge=1, le=20),
    threshold: float = Query(0.30, ge=0.0, le=1.0),
    rag: RagService = Depends(_rag_service),
) -> dict:
    return await rag.assist(case_id, k=k, score_threshold=threshold)


@router.post("/cases/{case_id}/ai-assist")
async def ai_assist(
    case_id: str,
    k: int = Query(5, ge=1, le=20),
    threshold: float = Query(0.30, ge=0.0, le=1.0),
    force: bool = Query(False, description="Bypass the freshness cache."),
    svc: AiAssistService = Depends(_ai_assist_service),
) -> dict:
    """Generate (or return cached) standardized ai_assist projection.

    Idempotent: subsequent calls within the freshness window return the
    same payload without round-tripping Bedrock. Set `force=true` to
    re-run.
    """
    return await svc.generate(case_id, k=k, threshold=threshold, force=force)


@router.get("/cases/{case_id}/ai-assist/trace")
async def ai_assist_trace(
    case_id: str,
    svc: AgentObservabilityService = Depends(_agent_observability_service),
) -> dict:
    """Return the LangGraph agent's per-node trace for a case.

    Shape::

        {
            "case_id": str,
            "has_trace": bool,
            "trace": [ {node, started_at, finished_at, duration_ms, ...}, ... ],
            "summary": {
                "path_taken": [str],
                "nodes_run": int,
                "error_count": int,
                "total_duration_ms": float | None,
            }
        }

    404 if the case doesn't exist; 200 with `has_trace=false` and empty
    arrays when the case exists but hasn't been driven through the
    agent yet (e.g. analyst opened it before the agent flag was on).
    """
    out = await svc.get_trace(case_id)
    if out is None:
        raise CaseNotFound(case_id)
    return out


@router.post("/cases/batch-ai-assist")
async def batch_ai_assist(
    payload: BatchAiAssistPayload,
    svc: AgentObservabilityService = Depends(_agent_observability_service),
) -> dict:
    """Drive `AiAssistService.generate` over up to 25 cases at once.

    Bounded internally by `asyncio.Semaphore(4)` (Bedrock quota
    awareness). Cached results count as `skipped`; fresh runs as
    `generated`. Per-case failures land in `errors[]` with a `reason`
    string — they do NOT short-circuit the whole batch.
    """
    return await svc.batch_generate(payload.case_ids, force=payload.force)
