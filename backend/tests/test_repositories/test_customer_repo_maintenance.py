"""PR-9 — CustomerRepository maintenance method tests.

Covers the five PR-9 maintenance methods on `CustomerRepository`:
  - push_brand_journey_event   (no slice, full retention)
  - push_support_interaction   ($slice: -20)
  - push_marketing_interaction ($slice: -50)
  - set_active_campaign_status (array_filters)
  - set_equipment_status       (array_filters)

All tests run against the in-memory `tests._fakes.FakeDB` so they exercise
the same MongoDB write idioms (`$push` with `$each`/`$slice`, `$set` with
`array_filters`) that the FakeDB implements.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import CUSTOMERS, CUSTOMERS_RESIDENTIAL
from app.repositories.customer_repo import CustomerRepository
from tests._fakes import FakeDB


_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _make_repo(db: FakeDB) -> CustomerRepository:
    return CustomerRepository(db)


# ---------------------------------------------------------------------
# push_brand_journey_event — full history retained
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_brand_journey_event_appends_with_no_slice() -> None:
    db = FakeDB()
    seed = [
        {"event": f"e{i}", "date": _NOW.isoformat()} for i in range(3)
    ]
    await db[CUSTOMERS].insert_one({
        "customer_id": "cust_R1",
        "brand_journey": list(seed),
    })

    repo = _make_repo(db)
    matched = await repo.push_brand_journey_event(
        "cust_R1", {"event": "newest", "date": _NOW.isoformat()}
    )
    assert matched == 1

    doc = await db[CUSTOMERS].find_one({"customer_id": "cust_R1"})
    journey = doc["brand_journey"]
    # Full history retained — 3 seed + 1 new = 4. No $slice cap.
    assert len(journey) == 4
    assert journey[-1]["event"] == "newest"
    # updated_at bumped.
    assert "updated_at" in doc


# ---------------------------------------------------------------------
# push_support_interaction — $slice: -20
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_support_interaction_trims_to_last_20() -> None:
    db = FakeDB()
    # Seed 25 existing tickets so the next push must trim.
    seed = [{"ticket_id": f"t{i}"} for i in range(25)]
    await db[CUSTOMERS].insert_one({
        "customer_id": "cust_R1",
        "interaction_history": {"support_tickets": list(seed)},
    })

    repo = _make_repo(db)
    matched = await repo.push_support_interaction(
        "cust_R1", {"ticket_id": "t_new"}
    )
    assert matched == 1

    doc = await db[CUSTOMERS].find_one({"customer_id": "cust_R1"})
    tickets = doc["interaction_history"]["support_tickets"]
    # 25 + 1 = 26, trimmed to last 20.
    assert len(tickets) == 20
    # The new ticket is the most-recent entry.
    assert tickets[-1]["ticket_id"] == "t_new"
    # The oldest seed tickets t0..t5 fell off the slice window.
    seen_ids = {t["ticket_id"] for t in tickets}
    assert "t0" not in seen_ids
    assert "t24" in seen_ids
    assert "updated_at" in doc


# ---------------------------------------------------------------------
# push_marketing_interaction — $slice: -50
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_marketing_interaction_trims_to_last_50() -> None:
    db = FakeDB()
    # Seed 60 existing events so the next push must trim.
    seed = [{"campaign_id": f"c{i}"} for i in range(60)]
    await db[CUSTOMERS].insert_one({
        "customer_id": "cust_R1",
        "interaction_history": {"marketing_events": list(seed)},
    })

    repo = _make_repo(db)
    matched = await repo.push_marketing_interaction(
        "cust_R1", {"campaign_id": "c_new"}
    )
    assert matched == 1

    doc = await db[CUSTOMERS].find_one({"customer_id": "cust_R1"})
    events = doc["interaction_history"]["marketing_events"]
    assert len(events) == 50
    assert events[-1]["campaign_id"] == "c_new"
    # The oldest events fell off the window.
    seen_ids = {e["campaign_id"] for e in events}
    assert "c0" not in seen_ids
    assert "c59" in seen_ids


# ---------------------------------------------------------------------
# set_active_campaign_status — array_filters flips one element only
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_active_campaign_status_only_touches_matched_element() -> None:
    db = FakeDB()
    await db[CUSTOMERS_RESIDENTIAL].insert_one({
        "customer_id": "cust_R1",
        "active_campaigns": [
            {"campaign_id": "camp_A", "status": "scheduled"},
            {"campaign_id": "camp_B", "status": "scheduled"},
            {"campaign_id": "camp_C", "status": "scheduled"},
        ],
    })

    repo = _make_repo(db)
    matched = await repo.set_active_campaign_status(
        "cust_R1", "camp_B", "in_flight", customer_type="residential"
    )
    assert matched == 1

    doc = await db[CUSTOMERS_RESIDENTIAL].find_one({"customer_id": "cust_R1"})
    by_id = {c["campaign_id"]: c for c in doc["active_campaigns"]}
    assert by_id["camp_A"]["status"] == "scheduled"
    assert by_id["camp_B"]["status"] == "in_flight"
    assert "updated_at" in by_id["camp_B"]
    assert by_id["camp_C"]["status"] == "scheduled"
    # Doc-level updated_at bumped too.
    assert "updated_at" in doc


# ---------------------------------------------------------------------
# set_equipment_status — array_filters flips one element only
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_equipment_status_only_touches_matched_element() -> None:
    db = FakeDB()
    await db[CUSTOMERS_RESIDENTIAL].insert_one({
        "customer_id": "cust_R1",
        "equipment": [
            {"equipment_id": "eq_1", "status": "active"},
            {"equipment_id": "eq_2", "status": "active"},
            {"equipment_id": "eq_3", "status": "active"},
        ],
    })

    repo = _make_repo(db)
    matched = await repo.set_equipment_status(
        "cust_R1", "eq_2", "swapped", customer_type="residential"
    )
    assert matched == 1

    doc = await db[CUSTOMERS_RESIDENTIAL].find_one({"customer_id": "cust_R1"})
    by_id = {e["equipment_id"]: e for e in doc["equipment"]}
    assert by_id["eq_1"]["status"] == "active"
    assert by_id["eq_2"]["status"] == "swapped"
    assert "updated_at" in by_id["eq_2"]
    assert by_id["eq_3"]["status"] == "active"
    assert "updated_at" in doc
