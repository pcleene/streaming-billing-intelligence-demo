"""PR-8 — CaseHistoryRepository.record_rag_usage tests.

Drives the new `record_rag_usage` method against an in-memory FakeDB
(no live Mongo). Verifies the positive/negative branches both bump
`used_in_rag_count`, refresh `last_used_in_rag_at`, and increment the
correct sub-counter under `rag_relevance_feedback` (which mirrors the
`RagFeedback` schema in `app/schemas/quarantine.py`).

The third test exercises the missing-row branch — the worker can call
us before the archive doc has been written, and that must be a no-op
rather than a hard error.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import QUARANTINE_CASES_HISTORY
from app.repositories.case_history_repo import CaseHistoryRepository
from tests._fakes import FakeDB


def _seed_history(db: FakeDB, *, case_id: str, **extra) -> dict:
    doc: dict = {
        "case_id": case_id,
        "customer_id": "cust-h-1",
        "disposition": "true_positive_refund",
        "severity": "medium",
        "analyst_notes": "expired promo",
        "resolution_summary": "refunded",
        "resolved_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "rules_triggered": [],
        "embedding": [0.0] * 8,
        "embedding_text": "...",
        "used_in_rag_count": 0,
        "rag_relevance_feedback": {"positive": 0, "negative": 0, "neutral": 0},
    }
    doc.update(extra)
    db[QUARANTINE_CASES_HISTORY]._docs.append(doc)
    return doc


@pytest.mark.asyncio
async def test_record_rag_usage_was_useful_increments_positive() -> None:
    db = FakeDB()
    _seed_history(db, case_id="case-h-1")
    repo = CaseHistoryRepository(db)

    matched = await repo.record_rag_usage("case-h-1", was_useful=True)

    assert matched == 1
    persisted = db[QUARANTINE_CASES_HISTORY]._docs[0]
    assert persisted["used_in_rag_count"] == 1
    assert persisted["rag_relevance_feedback"]["positive"] == 1
    assert persisted["rag_relevance_feedback"]["negative"] == 0
    last_used = persisted["last_used_in_rag_at"]
    assert isinstance(last_used, datetime)
    assert last_used.tzinfo is not None


@pytest.mark.asyncio
async def test_record_rag_usage_not_useful_increments_negative() -> None:
    db = FakeDB()
    _seed_history(db, case_id="case-h-2")
    repo = CaseHistoryRepository(db)

    # Two retrievals where neither one was confirmed useful (e.g. the
    # generation-time call from `AiAssistService.generate`).
    await repo.record_rag_usage("case-h-2", was_useful=False)
    await repo.record_rag_usage("case-h-2", was_useful=False)

    persisted = db[QUARANTINE_CASES_HISTORY]._docs[0]
    assert persisted["used_in_rag_count"] == 2
    assert persisted["rag_relevance_feedback"]["negative"] == 2
    assert persisted["rag_relevance_feedback"]["positive"] == 0


@pytest.mark.asyncio
async def test_record_rag_usage_missing_case_is_noop() -> None:
    db = FakeDB()
    # Don't seed anything — collection is empty.
    repo = CaseHistoryRepository(db)

    matched = await repo.record_rag_usage("never-existed", was_useful=True)

    assert matched == 0
    # No phantom doc should have been inserted (no upsert semantics).
    assert db[QUARANTINE_CASES_HISTORY]._docs == []
