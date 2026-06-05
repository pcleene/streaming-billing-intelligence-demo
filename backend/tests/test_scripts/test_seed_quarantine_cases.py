"""Unit tests for `scripts/seed_quarantine_cases.py` (PR-14).

The fake DB used across the suite covers the operations that other PR-1
scripts already lean on (find / insert_one / delete_one). PR-14's seed
module reaches for `delete_many` / `insert_many` because those map to
real Mongo's bulk surface; we patch those onto the FakeCollection at
test setup time so the fake behaves like a real bulk-capable collection
for the duration of these tests, without having to touch `_fakes.py`.
"""

from __future__ import annotations

import re

import pytest

from app.core.constants import CUSTOMERS_RESIDENTIAL, QUARANTINE_CASES
from scripts.seed_quarantine_cases import SEED_PREFIX, main
from tests._fakes import FakeCollection, FakeDB, _DeleteResult, _matches


# --- Bulk-op shim ------------------------------------------------------


class _InsertManyResult:
    def __init__(self, ids: list) -> None:
        self.inserted_ids = ids


async def _delete_many(self: FakeCollection, flt: dict) -> _DeleteResult:
    """Match the prefix regex used by `seed_quarantine_cases.main`."""
    keep: list[dict] = []
    removed = 0
    for d in self._docs:
        match = True
        for k, expected in (flt or {}).items():
            if isinstance(expected, dict) and "$regex" in expected:
                pattern = re.compile(expected["$regex"])
                actual = d.get(k)
                if not (isinstance(actual, str) and pattern.search(actual)):
                    match = False
                    break
            else:
                if not _matches(d, {k: expected}):
                    match = False
                    break
        if match:
            removed += 1
        else:
            keep.append(d)
    self._docs = keep
    return _DeleteResult(removed)


async def _insert_many(
    self: FakeCollection, docs: list[dict], ordered: bool = False
) -> _InsertManyResult:
    import copy

    ids: list = []
    for d in docs:
        self._docs.append(copy.deepcopy(d))
        ids.append(d.get("_id"))
    return _InsertManyResult(ids)


@pytest.fixture(autouse=True)
def _patch_fake_bulk_ops(monkeypatch):
    """Add `delete_many` + `insert_many` to FakeCollection for these tests."""
    monkeypatch.setattr(FakeCollection, "delete_many", _delete_many, raising=False)
    monkeypatch.setattr(FakeCollection, "insert_many", _insert_many, raising=False)
    yield


# --- Helpers -----------------------------------------------------------


def _seed_customers(db: FakeDB, n: int) -> None:
    for i in range(n):
        db[CUSTOMERS_RESIDENTIAL]._docs.append(
            {"customer_id": f"cust_{i:06d}", "customer_type": "residential"}
        )


# --- Tests -------------------------------------------------------------


async def test_seed_inserts_default_count_with_seed_prefix() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    counters = await main(db)

    assert counters["inserted"] == 25
    assert counters["deleted"] == 0
    docs = db[QUARANTINE_CASES]._docs
    assert len(docs) == 25
    for d in docs:
        assert d["case_id"].startswith(SEED_PREFIX)


async def test_seed_is_idempotent_on_rerun() -> None:
    """Running main twice yields the same final case count."""
    db = FakeDB()
    _seed_customers(db, 50)

    first = await main(db)
    second = await main(db)

    assert first["inserted"] == 25
    assert second["inserted"] == 25
    assert second["deleted"] == 25
    # Final doc count stable across reruns thanks to the prefix-scoped
    # delete_many.
    assert len(db[QUARANTINE_CASES]._docs) == 25


async def test_severity_mix_roughly_matches_spec() -> None:
    """40% medium, 30% high, 20% low, 10% critical with ±2 tolerance."""
    db = FakeDB()
    _seed_customers(db, 50)

    counters = await main(db, count=25)

    # Expected counts (out of 25): medium=10, high=7-8, low=5, critical=2-3.
    expected = {"medium": 10, "high": 7, "low": 5, "critical": 3}
    for sev, want in expected.items():
        got = counters[f"by_severity_{sev}"]
        assert abs(got - want) <= 2, (
            f"severity {sev}: got {got}, expected ~{want} (±2)"
        )


async def test_about_60pct_have_ai_assist() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    counters = await main(db, count=25)

    # 60% of 25 ≈ 15. With seed=42 RNG the actual fraction is in a tight
    # band; allow ±5 for stochastic wiggle.
    assert 10 <= counters["with_ai_assist"] <= 20


async def test_cases_with_ai_assist_have_non_empty_agent_trace() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    await main(db, count=25)

    enriched = [
        d for d in db[QUARANTINE_CASES]._docs if d.get("ai_assist") is not None
    ]
    assert enriched, "expected at least one case to carry ai_assist"
    for d in enriched:
        trace = d["ai_assist"].get("agent_trace")
        assert isinstance(trace, list) and len(trace) >= 3
        for entry in trace:
            assert "node" in entry
            assert "started_at" in entry
            assert "duration_ms" in entry
            assert isinstance(entry["duration_ms"], float)


async def test_at_least_one_case_is_breached_with_consistent_sla_window() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    counters = await main(db, count=25)

    breached = [
        d for d in db[QUARANTINE_CASES]._docs
        if (d.get("sla") or {}).get("is_breached")
    ]
    assert counters["breached"] >= 1
    assert breached, "expected at least one breached case"
    for d in breached:
        sla = d["sla"]
        assert sla["breached_at"] is not None
        # Invariant: target_resolution_at sits between opened_at and now.
        opened_at = d["opened_at"]
        target = sla["target_resolution_at"]
        assert target > opened_at
        # Breached means now is past target.
        assert sla["breached_at"] > target or sla["minutes_to_breach"] <= 0


async def test_priority_band_is_one_of_p0_p1_p2_p3() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    await main(db, count=25)

    bands = {d["priority_band"] for d in db[QUARANTINE_CASES]._docs}
    assert bands.issubset({"P0", "P1", "P2", "P3"})


async def test_lifecycle_events_non_empty_for_every_case() -> None:
    db = FakeDB()
    _seed_customers(db, 50)

    await main(db, count=25)

    for d in db[QUARANTINE_CASES]._docs:
        events = d.get("lifecycle_events")
        assert isinstance(events, list) and len(events) >= 1
        # First event opens the case.
        assert events[0]["to_status"] == "open"


async def test_synthetic_customer_ids_used_when_collection_empty() -> None:
    """No residential customers seeded → falls back to synthetic ids."""
    db = FakeDB()

    counters = await main(db, count=10)

    assert counters["inserted"] == 10
    for d in db[QUARANTINE_CASES]._docs:
        # Synthetic prefix from _build_case fallback path.
        assert d["customer_id"].startswith("cust_")


async def test_count_parameter_is_honoured() -> None:
    db = FakeDB()
    _seed_customers(db, 5)

    counters = await main(db, count=8)

    assert counters["inserted"] == 8
    assert len(db[QUARANTINE_CASES]._docs) == 8
