"""Unit tests for `scripts/seed_system_metrics.py` (PR-14).

Drives `seed_system_metrics.main` directly with an in-memory fake DB
and validates the shape, ordering, and idempotency of the seeded
`system_metrics` documents.

The shared FakeCollection in `tests/_fakes.py` does not implement
`delete_many` or the `$regex` operator, so this module ships a
narrowly-scoped subclass (`_RegexAwareCollection`) that backs the
seed module's idempotency call. We deliberately do NOT modify the
shared fake — keeping the extension test-local avoids leaking new
operators to other tests.
"""

from __future__ import annotations

import re

import pytest

from app.core.constants import SYSTEM_METRICS
from scripts import seed_system_metrics
from tests._fakes import FakeCollection, FakeDB


class _RegexAwareCollection(FakeCollection):
    """FakeCollection extension that supports `delete_many` + `$regex`."""

    async def delete_many(self, flt: dict) -> object:
        survivors: list[dict] = []
        deleted = 0
        for doc in self._docs:
            if _matches_with_regex(doc, flt):
                deleted += 1
            else:
                survivors.append(doc)
        self._docs = survivors

        class _R:
            def __init__(self, n: int) -> None:
                self.deleted_count = n

        return _R(deleted)


def _matches_with_regex(doc: dict, flt: dict | None) -> bool:
    if not flt:
        return True
    for key, expected in flt.items():
        if key == "$or":
            if not any(_matches_with_regex(doc, sub) for sub in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches_with_regex(doc, sub) for sub in expected):
                return False
            continue
        actual = doc
        for part in key.split("."):
            if not isinstance(actual, dict):
                actual = None
                break
            actual = actual.get(part)
        if isinstance(expected, dict):
            for op, val in expected.items():
                if op == "$regex":
                    if actual is None or not re.search(val, str(actual)):
                        return False
                elif op == "$exists":
                    if (actual is not None) != bool(val):
                        return False
                else:
                    # Defer to plain-equality for anything else; the seed
                    # module only needs $regex/$or here.
                    if actual != val:
                        return False
        elif actual != expected:
            return False
    return True


class _RegexAwareDB(FakeDB):
    def __getitem__(self, name: str) -> _RegexAwareCollection:  # type: ignore[override]
        if name not in self._collections:
            self._collections[name] = _RegexAwareCollection(name)
        return self._collections[name]  # type: ignore[return-value]


@pytest.fixture
def db() -> _RegexAwareDB:
    return _RegexAwareDB()


@pytest.mark.asyncio
async def test_seed_returns_expected_counters(db):
    counters = await seed_system_metrics.main(db, samples_burst=60, samples_steady=40)
    assert counters["burst_inserted"] == 60
    assert counters["steady_inserted"] == 40
    assert counters["total_inserted"] == 100
    assert counters["burst_run_id"].startswith("seed_burst_")


@pytest.mark.asyncio
async def test_idempotent_double_run_yields_same_count(db):
    await seed_system_metrics.main(db, samples_burst=20, samples_steady=15)
    after_first = await db[SYSTEM_METRICS].count_documents({})
    await seed_system_metrics.main(db, samples_burst=20, samples_steady=15)
    after_second = await db[SYSTEM_METRICS].count_documents({})
    assert after_first == after_second == 35


@pytest.mark.asyncio
async def test_burst_sample_count_matches_param(db):
    await seed_system_metrics.main(db, samples_burst=42, samples_steady=10)
    burst_count = await db[SYSTEM_METRICS].count_documents({"mode": "burst"})
    assert burst_count == 42


@pytest.mark.asyncio
async def test_steady_sample_count_matches_param(db):
    await seed_system_metrics.main(db, samples_burst=10, samples_steady=27)
    steady_count = await db[SYSTEM_METRICS].count_documents({"mode": "steady"})
    assert steady_count == 27


@pytest.mark.asyncio
async def test_all_burst_samples_share_run_id(db):
    counters = await seed_system_metrics.main(db, samples_burst=15, samples_steady=5)
    coll = db[SYSTEM_METRICS]
    burst_docs = [d for d in coll._docs if d["mode"] == "burst"]
    assert burst_docs, "expected at least one burst doc"
    assert all(d["burst_run_id"] == counters["burst_run_id"] for d in burst_docs)
    # Steady samples must NOT carry a burst_run_id.
    steady_docs = [d for d in coll._docs if d["mode"] == "steady"]
    assert all(d["burst_run_id"] is None for d in steady_docs)


@pytest.mark.asyncio
async def test_burst_window_strictly_after_steady_window(db):
    await seed_system_metrics.main(db, samples_burst=12, samples_steady=8)
    coll = db[SYSTEM_METRICS]
    burst_times = [d["recorded_at"] for d in coll._docs if d["mode"] == "burst"]
    steady_times = [d["recorded_at"] for d in coll._docs if d["mode"] == "steady"]
    assert min(burst_times) > max(steady_times)


@pytest.mark.asyncio
async def test_peak_observed_tps_clears_200(db):
    await seed_system_metrics.main(db, samples_burst=60, samples_steady=10)
    coll = db[SYSTEM_METRICS]
    burst_docs = [d for d in coll._docs if d["mode"] == "burst"]
    peak = max(d["observed_tps"] for d in burst_docs)
    assert peak >= 200, f"expected burst peak >= 200, got {peak}"


@pytest.mark.asyncio
async def test_p99_always_at_least_p50(db):
    await seed_system_metrics.main(db, samples_burst=30, samples_steady=20)
    coll = db[SYSTEM_METRICS]
    for d in coll._docs:
        assert d["p99_ms_ingest"] >= d["p50_ms_ingest"], d


@pytest.mark.asyncio
async def test_all_docs_carry_seed_marker(db):
    await seed_system_metrics.main(db, samples_burst=8, samples_steady=8)
    coll = db[SYSTEM_METRICS]
    assert coll._docs
    assert all(d.get("_seed_marker") == "pr14" for d in coll._docs)


@pytest.mark.asyncio
async def test_recorded_at_is_utc_aware(db):
    await seed_system_metrics.main(db, samples_burst=5, samples_steady=5)
    coll = db[SYSTEM_METRICS]
    for d in coll._docs:
        ts = d["recorded_at"]
        assert ts.tzinfo is not None, "recorded_at must be tz-aware"
        assert ts.utcoffset().total_seconds() == 0
