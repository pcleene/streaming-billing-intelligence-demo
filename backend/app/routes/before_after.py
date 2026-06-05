"""Before/after panel route (Phase B.7).

Mongo-stream vs. warehouse-batch comparison for a single case.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import DBDep
from app.schemas.before_after import BeforeAfter
from app.services.before_after_service import BeforeAfterService

router = APIRouter(prefix="/api/cases", tags=["before-after"])


def _service(db: DBDep) -> BeforeAfterService:
    return BeforeAfterService(db)


@router.get("/{case_id}/before-after", response_model=BeforeAfter)
async def get_before_after(
    case_id: str,
    svc: BeforeAfterService = Depends(_service),
) -> BeforeAfter:
    return await svc.get(case_id)
