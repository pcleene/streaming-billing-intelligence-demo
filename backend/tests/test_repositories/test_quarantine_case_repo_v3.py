"""PR-7 — V3 lifecycle tests for QuarantineCaseRepository.

Drives `open_case` / `transition_status` / `resolve` / `apply_sla_update`
/ `sweep_sla` / `list_open` against an in-memory FakeDB so we can
verify the V3 case shape, the `customers.open_cases` mirror, lifecycle
events, and SLA bookkeeping without spinning up Mongo.

Sits alongside `test_quarantine_case_repo_embed.py` (PR-1 lean surface)
which must remain green.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.constants import (
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    QUARANTINE_CASES,
    SCHEMA_VERSION_V3,
)
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from tests._fakes import FakeDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_customer(
    db: FakeDB,
    *,
    customer_id: str,
    customer_type: str = "residential",
    **extra,
) -> dict:
    """Seed a minimal customer doc into the appropriate typed collection.

    Returns the seeded dict so callers can pass it to `open_case` as
    `customer_doc`.
    """
    coll_name = (
        CUSTOMERS_COMMERCIAL if customer_type == "commercial" else CUSTOMERS
    )
    doc: dict = {
        "customer_id": customer_id,
        "customer_type": customer_type,
        "lifetime_quarantine_count": 0,
        "address": {"state": "Selangor"},
    }
    doc.update(extra)
    db[coll_name]._docs.append(doc)
    return doc


async def _open_case(
    repo: QuarantineCaseRepository,
    *,
    case_id: str = "case_1",
    customer_id: str = "cust_1",
    severity: str = "high",
    rules_triggered: list[dict] | None = None,
    customer_doc: dict | None = None,
    transaction_summary: dict | None = None,
    transaction_id: str | None = "txn_1",
    actor_id: str = "system",
    sync_customer_embed: bool = True,
    **kw,
) -> dict:
    """Thin wrapper around `repo.open_case` with sensible defaults."""
    if rules_triggered is None:
        rules_triggered = [
            {
                "rule_type": "velocity_anomaly",
                "rule_name": "x",
                "severity": severity,
            }
        ]
    if transaction_summary is None:
        transaction_summary = {"total_myr": 1500.0}
    return await repo.open_case(
        case_id=case_id,
        customer_id=customer_id,
        severity=severity,
        rules_triggered=rules_triggered,
        customer_doc=customer_doc,
        transaction_summary=transaction_summary,
        transaction_id=transaction_id,
        actor_id=actor_id,
        sync_customer_embed=sync_customer_embed,
        **kw,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_open_case_inserts_v3_shape_and_mirrors_embed() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(
        db,
        customer_id="cust_1",
        lifetime_quarantine_count=2,
    )

    case = await _open_case(repo, customer_doc=customer)

    assert case["_schema_version"] == SCHEMA_VERSION_V3
    assert case["status"] == "open"
    assert case["priority_band"] in {"P0", "P1", "P2", "P3"}
    assert 0 <= case["priority_score"] <= 1
    assert len(case["lifecycle"]) == 1
    assert case["lifecycle"][0]["to_status"] == "open"
    assert isinstance(case["sla"]["target_resolution_at"], datetime)
    assert case["revenue_impact"]["amount_at_risk_myr"] == 1500.0
    assert case["auto_priority_drivers"]

    # The parent doc landed in quarantine_cases.
    assert any(
        d.get("case_id") == "case_1"
        for d in db[QUARANTINE_CASES]._docs
    )

    # Customer embed mirrored + lifetime counter bumped.
    customer_doc = db[CUSTOMERS]._docs[0]
    open_cases = customer_doc.get("open_cases") or []
    assert any(emb.get("case_id") == "case_1" for emb in open_cases)
    assert customer_doc["lifetime_quarantine_count"] == 3


async def test_open_case_skips_embed_when_disabled() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(
        db,
        customer_id="cust_1",
        lifetime_quarantine_count=2,
    )

    await _open_case(repo, customer_doc=customer, sync_customer_embed=False)

    customer_doc = db[CUSTOMERS]._docs[0]
    assert "open_cases" not in customer_doc or not customer_doc["open_cases"]
    # Counter not incremented.
    assert customer_doc["lifetime_quarantine_count"] == 2


async def test_transition_status_appends_lifecycle_and_syncs_embed() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    case = await repo.transition_status(
        "case_1", to_status="under_review", actor_id="alice"
    )

    assert case["status"] == "under_review"
    assert len(case["lifecycle"]) == 2
    last = case["lifecycle"][-1]
    assert last["to_status"] == "under_review"
    assert last["from_status"] == "open"
    assert last["actor_id"] == "alice"

    # Embed status mirrored on the customer doc.
    customer_doc = db[CUSTOMERS]._docs[0]
    assert customer_doc["open_cases"][0]["status"] == "under_review"


async def test_transition_status_rejects_terminal() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    with pytest.raises(ValueError):
        await repo.transition_status(
            "case_1", to_status="resolved", actor_id="x"
        )


async def test_transition_status_unknown_case_raises_keyerror() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)

    with pytest.raises(KeyError):
        await repo.transition_status(
            "nope", to_status="under_review", actor_id="x"
        )


async def test_transition_status_idempotent_when_no_change() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    case = await repo.transition_status(
        "case_1", to_status="open", actor_id="x"
    )

    # No new lifecycle event appended for a no-op transition.
    assert case["status"] == "open"
    assert len(case["lifecycle"]) == 1


async def test_resolve_marks_terminal_pulls_embed_and_appends_lifecycle() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    case = await repo.resolve(
        "case_1",
        disposition="legitimate",
        analyst_id="alice",
        analyst_notes="fp",
        terminal_status="resolved",
    )

    assert case["status"] == "resolved"
    assert case["disposition"] == "legitimate"
    assert case["resolved_by"] == "alice"
    assert case["resolved_at"] is not None
    last = case["lifecycle"][-1]
    assert last["to_status"] == "resolved"
    assert last["from_status"] == "open"

    # Embed pulled from customer.
    customer_doc = db[CUSTOMERS]._docs[0]
    open_cases = customer_doc.get("open_cases") or []
    assert all(emb.get("case_id") != "case_1" for emb in open_cases)


async def test_resolve_dismissed_also_works() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    case = await repo.resolve(
        "case_1",
        disposition="false_positive",
        analyst_id="alice",
        terminal_status="dismissed",
    )

    assert case["status"] == "dismissed"
    customer_doc = db[CUSTOMERS]._docs[0]
    open_cases = customer_doc.get("open_cases") or []
    assert all(emb.get("case_id") != "case_1" for emb in open_cases)


async def test_resolve_rejects_non_terminal_status() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    with pytest.raises(ValueError):
        await repo.resolve(
            "case_1",
            disposition="x",
            analyst_id="y",
            terminal_status="open",
        )


async def test_apply_sla_update_persists_fragment() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    customer = _seed_customer(db, customer_id="cust_1")
    await _open_case(repo, customer_doc=customer)

    target = datetime.now(timezone.utc) + timedelta(minutes=230)
    sla_fragment = {
        "target_resolution_at": target,
        "elapsed_minutes": 12,
        "minutes_to_breach": 230,
        "is_breached": False,
        "breached_at": None,
    }
    await repo.apply_sla_update("case_1", sla=sla_fragment)

    persisted = await repo.get_by_id("case_1")
    assert persisted is not None
    assert persisted["sla"]["elapsed_minutes"] == 12
    assert persisted["sla"]["minutes_to_breach"] == 230
    assert persisted["sla"]["target_resolution_at"] == target
    assert persisted["sla"]["is_breached"] is False


async def test_sweep_sla_updates_open_cases_and_counts_breaches() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    _seed_customer(db, customer_id="cust_1")
    _seed_customer(db, customer_id="cust_2")
    _seed_customer(db, customer_id="cust_3")

    customer1 = db[CUSTOMERS]._docs[0]
    customer2 = db[CUSTOMERS]._docs[1]
    customer3 = db[CUSTOMERS]._docs[2]

    await _open_case(
        repo,
        case_id="case_crit_1",
        customer_id="cust_1",
        severity="critical",
        customer_doc=customer1,
    )
    await _open_case(
        repo,
        case_id="case_crit_2",
        customer_id="cust_2",
        severity="critical",
        customer_doc=customer2,
    )
    await _open_case(
        repo,
        case_id="case_med_1",
        customer_id="cust_3",
        severity="medium",
        customer_doc=customer3,
    )

    # Force the two critical cases into the deep past so their 30-min SLA
    # has already breached at "now". We must also rewind
    # `sla.target_resolution_at` because the SLA target is captured at
    # `open_case` time (`now + 30min`) and `apply_sla_tick` evaluates
    # breach against that frozen target, not against `created_at`.
    deep_past = datetime(2025, 1, 1, tzinfo=timezone.utc)
    deep_past_target = deep_past + timedelta(minutes=30)
    for d in db[QUARANTINE_CASES]._docs:
        if d["case_id"] in ("case_crit_1", "case_crit_2"):
            d["created_at"] = deep_past
            d["sla"]["target_resolution_at"] = deep_past_target

    counters = await repo.sweep_sla(now=datetime.now(timezone.utc))

    assert counters["scanned"] >= 3
    assert counters["newly_breached"] == 2
    assert counters["updated"] >= 2

    breached = [
        d for d in db[QUARANTINE_CASES]._docs
        if d["case_id"] in ("case_crit_1", "case_crit_2")
    ]
    for d in breached:
        assert d["sla"]["is_breached"] is True
        assert d["sla"]["breached_at"] is not None


async def test_list_open_excludes_terminal() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)
    _seed_customer(db, customer_id="cust_1")
    _seed_customer(db, customer_id="cust_2")
    _seed_customer(db, customer_id="cust_3")

    await _open_case(
        repo,
        case_id="case_a",
        customer_id="cust_1",
        customer_doc=db[CUSTOMERS]._docs[0],
    )
    await _open_case(
        repo,
        case_id="case_b",
        customer_id="cust_2",
        customer_doc=db[CUSTOMERS]._docs[1],
    )
    await _open_case(
        repo,
        case_id="case_c",
        customer_id="cust_3",
        customer_doc=db[CUSTOMERS]._docs[2],
    )

    await repo.resolve(
        "case_b",
        disposition="legitimate",
        analyst_id="alice",
        terminal_status="resolved",
    )

    open_cases = await repo.list_open()
    assert len(open_cases) == 2
    open_ids = {c["case_id"] for c in open_cases}
    assert "case_b" not in open_ids


async def test_mirror_collection_for_typed_customer() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)

    commercial_doc = {
        "customer_id": "cust_c",
        "customer_type": "commercial",
        "lifetime_quarantine_count": 0,
    }
    db[CUSTOMERS_COMMERCIAL]._docs.append(commercial_doc)

    await _open_case(
        repo,
        case_id="case_c1",
        customer_id="cust_c",
        customer_doc=commercial_doc,
    )

    # Embed pushed to commercial collection.
    pushed = db[CUSTOMERS_COMMERCIAL]._docs[0].get("open_cases") or []
    assert any(emb.get("case_id") == "case_c1" for emb in pushed)

    # Residential alias collection should not have received this case.
    res_docs = db[CUSTOMERS]._docs
    for d in res_docs:
        for emb in d.get("open_cases") or []:
            assert emb.get("case_id") != "case_c1"
