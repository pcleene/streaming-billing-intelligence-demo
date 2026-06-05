"""Quarantine case lifecycle service.

Owns the operational quarantine_cases collection (live cases). Resolved
cases get archived to quarantine_cases_history (the RAG corpus) by the
disposition flow once analyst dispositions them.

AutoEmbed (ADR-032): the archive write composes `embed_source.text`
from the resolved-case context and stamps `_schema_version=4`. Atlas
embeds the text invisibly via the AutoEmbed index — there is no
application-side Voyage call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.constants import SCHEMA_VERSION_V4
from app.core.errors import CaseNotFound
from app.core.logging import get_logger
from app.repositories.case_history_repo import CaseHistoryRepository
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.services.embed_text_builder import build_history_embed_text

logger = get_logger(__name__)


class QuarantineService:
    def __init__(
        self,
        case_repo: QuarantineCaseRepository,
        history_repo: CaseHistoryRepository,
    ) -> None:
        self._cases = case_repo
        self._history = history_repo

    async def list_cases(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        rule_type: str | None = None,
        agent_reviewed: bool | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        items = await self._cases.list_paged(
            status=status,
            severity=severity,
            rule_type=rule_type,
            agent_reviewed=agent_reviewed,
            skip=skip,
            limit=limit,
        )
        # Drop the BSON `_id` so the FastAPI JSON encoder doesn't trip over
        # `ObjectId`. The repo intentionally keeps it for callers that need
        # to update by `_id`; the route returns plain JSON.
        items = [{k: v for k, v in d.items() if k != "_id"} for d in items]
        total = await self._cases.count_filtered(
            status=status,
            severity=severity,
            rule_type=rule_type,
            agent_reviewed=agent_reviewed,
        )
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    async def get_case(self, case_id: str) -> dict:
        case = await self._cases.get_by_id(case_id)
        if not case:
            raise CaseNotFound(case_id)
        # Mirror `list_cases`: strip Mongo `_id` (BSON ObjectId) so the
        # route can serialize this as plain JSON. Repo keeps `_id` for
        # write-side callers.
        return {k: v for k, v in case.items() if k != "_id"}

    async def disposition(
        self,
        case_id: str,
        *,
        disposition: str,
        analyst_id: str,
        analyst_notes: str = "",
    ) -> dict:
        case = await self.get_case(case_id)
        resolved_at = datetime.now(timezone.utc)
        new_status = "resolved" if disposition in {"true_positive", "false_positive"} else "under_review"

        await self._cases.update_disposition(
            case_id,
            disposition=disposition,
            analyst_id=analyst_id,
            analyst_notes=analyst_notes,
            status=new_status,
            resolved_at=resolved_at if new_status == "resolved" else None,
        )

        # Archive to RAG corpus once resolved.
        if new_status == "resolved":
            await self._archive_to_history(case, disposition, analyst_id, analyst_notes, resolved_at)

        return await self.get_case(case_id)

    async def _archive_to_history(
        self,
        case: dict,
        disposition: str,
        analyst_id: str,
        analyst_notes: str,
        resolved_at: datetime,
    ) -> None:
        archived = {
            **{k: v for k, v in case.items() if k != "_id"},
            "disposition": disposition,
            "analyst_id": analyst_id,
            "analyst_notes": analyst_notes,
            "resolved_at": resolved_at,
            "_schema_version": SCHEMA_VERSION_V4,
        }
        # AutoEmbed: write the source text only — Atlas owns the vector.
        text = build_history_embed_text(archived)
        archived["embed_source"] = {"text": text}

        await self._history.upsert_resolved(archived)
