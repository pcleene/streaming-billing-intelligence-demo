"""Analyst override + RAG audit endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import DBDep
from app.repositories.quarantine_case_repo import QuarantineCaseRepository

router = APIRouter(prefix="/api/analyst", tags=["analyst"])


class OverridePayload(BaseModel):
    case_id: str
    analyst_id: str
    override_disposition: str
    rationale: str
    ai_assist_was_used: bool = True


def _case_repo(db: DBDep) -> QuarantineCaseRepository:
    return QuarantineCaseRepository(db)


@router.post("/override")
async def record_override(
    payload: OverridePayload,
    repo: QuarantineCaseRepository = Depends(_case_repo),
) -> dict:
    """Record an analyst-vs-AI disagreement for the model-feedback loop."""
    await repo.attach_ai_assist(
        payload.case_id,
        {
            "analyst_override": {
                "analyst_id": payload.analyst_id,
                "override_disposition": payload.override_disposition,
                "rationale": payload.rationale,
                "ai_assist_was_used": payload.ai_assist_was_used,
                "recorded_at": datetime.now(timezone.utc),
            }
        },
    )
    return {"status": "recorded", "case_id": payload.case_id}
