"""`list_paged(agent_reviewed=...)` filter for the quarantine repo.

Drives the FakeDB-backed repo with a mix of:
- cases that carry a non-empty `ai_assist.agent_trace` (agent-reviewed),
- cases with `ai_assist` set but an empty trace,
- cases with `ai_assist` missing/None.

Verifies True / False / None semantics + that the parallel
`count_filtered` returns matching cardinality.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import QUARANTINE_CASES
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from tests._fakes import FakeDB


def _seed_case(
    db: FakeDB,
    *,
    case_id: str,
    ai_assist: dict | None,
    severity: str = "high",
    status: str = "open",
) -> None:
    db[QUARANTINE_CASES]._docs.append({
        "case_id": case_id,
        "customer_id": f"cust_{case_id}",
        "severity": severity,
        "status": status,
        "priority_score": 0,
        "ai_assist": ai_assist,
        "rules_triggered": [],
        "created_at": datetime.now(timezone.utc),
    })


@pytest.mark.asyncio
async def test_list_paged_agent_reviewed_true_filters_to_traced_cases() -> None:
    db = FakeDB()
    _seed_case(db, case_id="reviewed_1", ai_assist={"agent_trace": [{"node": "classify"}]})
    _seed_case(db, case_id="reviewed_2", ai_assist={"agent_trace": [{"node": "retrieve"}, {"node": "synthesize"}]})
    _seed_case(db, case_id="empty_trace", ai_assist={"agent_trace": []})
    _seed_case(db, case_id="no_assist", ai_assist=None)
    _seed_case(db, case_id="missing_assist_field", ai_assist=None)  # ai_assist None path

    repo = QuarantineCaseRepository(db)

    rows = await repo.list_paged(agent_reviewed=True)
    ids = sorted(r["case_id"] for r in rows)
    assert ids == ["reviewed_1", "reviewed_2"]

    total = await repo.count_filtered(agent_reviewed=True)
    assert total == 2


@pytest.mark.asyncio
async def test_list_paged_agent_reviewed_false_returns_inverse() -> None:
    db = FakeDB()
    _seed_case(db, case_id="reviewed_1", ai_assist={"agent_trace": [{"node": "n"}]})
    _seed_case(db, case_id="empty_trace", ai_assist={"agent_trace": []})
    _seed_case(db, case_id="no_assist", ai_assist=None)

    repo = QuarantineCaseRepository(db)

    rows = await repo.list_paged(agent_reviewed=False)
    ids = sorted(r["case_id"] for r in rows)
    assert ids == ["empty_trace", "no_assist"]
    assert await repo.count_filtered(agent_reviewed=False) == 2


@pytest.mark.asyncio
async def test_list_paged_agent_reviewed_none_is_unfiltered() -> None:
    db = FakeDB()
    _seed_case(db, case_id="a", ai_assist={"agent_trace": [{}]})
    _seed_case(db, case_id="b", ai_assist=None)

    repo = QuarantineCaseRepository(db)
    rows = await repo.list_paged()  # default agent_reviewed=None
    assert {r["case_id"] for r in rows} == {"a", "b"}
    assert await repo.count_filtered() == 2
