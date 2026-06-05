"""Unit tests for QuarantineCaseRepository's customers.open_cases lifecycle.

Three transitions:
  - sync_open_case_to_customer       → $push embed, $inc lifetime_quarantine_count
  - update_open_case_status_on_customer → array_filters status flip
  - remove_open_case_from_customer   → $pull by case_id
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import CUSTOMERS, QUARANTINE_CASES
from app.repositories.quarantine_case_repo import (
    EMBED_OPEN_STATUSES,
    QuarantineCaseRepository,
    _build_open_case_embed,
)


class _FakeUpdateResult:
    def __init__(self, modified: int = 1) -> None:
        self.modified_count = modified


def _make_repo() -> tuple[QuarantineCaseRepository, AsyncMock]:
    customers_coll = MagicMock()
    customers_coll.update_one = AsyncMock(return_value=_FakeUpdateResult(1))
    cases_coll = MagicMock()
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: {
        CUSTOMERS: customers_coll,
        QUARANTINE_CASES: cases_coll,
    }[name]
    repo = QuarantineCaseRepository(db)
    return repo, customers_coll.update_one


def _case(case_id: str = "case_1", status: str = "open") -> dict:
    return {
        "case_id": case_id,
        "customer_id": "cust_1",
        "transaction_id": "txn_1",
        "severity": "high",
        "status": status,
        "rules_triggered": [
            {"rule_type": "discount_mismatch", "score": 0.92},
            {"rule_type": "velocity_anomaly", "score": 0.81},
        ],
        "created_at": datetime(2026, 5, 4, 9, 0, 0),
        "updated_at": datetime(2026, 5, 4, 9, 0, 0),
    }


@pytest.mark.asyncio
async def test_sync_open_case_pushes_and_increments_lifetime() -> None:
    repo, update_one = _make_repo()
    modified = await repo.sync_open_case_to_customer(_case())

    assert modified == 1
    args, _ = update_one.call_args
    filter_, update = args
    assert filter_ == {"customer_id": "cust_1"}
    assert update["$inc"] == {"lifetime_quarantine_count": 1}
    embed = update["$push"]["open_cases"]
    assert embed["case_id"] == "case_1"
    # Rule types are deduped + sorted from rules_triggered.
    assert embed["rule_types"] == ["discount_mismatch", "velocity_anomaly"]


@pytest.mark.asyncio
async def test_update_status_uses_array_filters() -> None:
    repo, update_one = _make_repo()
    modified = await repo.update_open_case_status_on_customer(
        customer_id="cust_1", case_id="case_1", status="under_review"
    )
    assert modified == 1
    args, kwargs = update_one.call_args
    filter_, update = args
    assert filter_ == {"customer_id": "cust_1"}
    assert update["$set"]["open_cases.$[c].status"] == "under_review"
    assert kwargs["array_filters"] == [{"c.case_id": "case_1"}]


@pytest.mark.asyncio
async def test_update_status_rejects_terminal_status() -> None:
    repo, _ = _make_repo()
    with pytest.raises(ValueError):
        await repo.update_open_case_status_on_customer(
            customer_id="cust_1", case_id="case_1", status="resolved"
        )


@pytest.mark.asyncio
async def test_remove_pulls_by_case_id() -> None:
    repo, update_one = _make_repo()
    modified = await repo.remove_open_case_from_customer(
        customer_id="cust_1", case_id="case_1"
    )
    assert modified == 1
    args, _ = update_one.call_args
    filter_, update = args
    assert filter_ == {"customer_id": "cust_1"}
    assert update == {"$pull": {"open_cases": {"case_id": "case_1"}}}


def test_open_statuses_constant() -> None:
    """The status whitelist matches the schema — open + under_review only."""
    assert set(EMBED_OPEN_STATUSES) == {"open", "under_review"}


def test_build_open_case_embed_handles_missing_rule_types() -> None:
    case = _case()
    case["rules_triggered"] = [{"score": 1.0}]  # malformed rule entry
    embed = _build_open_case_embed(case)
    assert embed["rule_types"] == []
