"""PR-10 — feature repository lineage / quality write tests.

Exercises the new ``update_lineage``, ``write_quality``, and
``get_with_lineage`` methods on :class:`FeatureRepository` using the
:class:`tests._fakes.FakeDB` in-memory async-Mongo stand-in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.repositories.feature_repo import FeatureRepository
from tests._fakes import FakeDB

_TS_OLD = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)
_TS_MID = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
_TS_NEW = datetime(2026, 5, 7, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def repo(db: FakeDB) -> FeatureRepository:
    return FeatureRepository(db)


# --- update_lineage ---------------------------------------------------


@pytest.mark.asyncio
async def test_update_lineage_increments_count(
    repo: FeatureRepository,
) -> None:
    """Two consecutive calls should leave the counter at 2 (1 + 1)."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})

    matched = await repo.update_lineage(cid, source_txn_at=_TS_OLD)
    assert matched == 1
    matched = await repo.update_lineage(cid, source_txn_at=_TS_MID)
    assert matched == 1

    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["lineage"]["source_transactions_count"] == 2


@pytest.mark.asyncio
async def test_update_lineage_tracks_min_max_timestamps(
    repo: FeatureRepository,
) -> None:
    """``$min`` / ``$max`` semantics: window only widens, never shrinks."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})

    # Older then newer — earliest pinned to OLD, latest to NEW.
    await repo.update_lineage(cid, source_txn_at=_TS_OLD)
    await repo.update_lineage(cid, source_txn_at=_TS_NEW)

    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["lineage"]["earliest_source_at"] == _TS_OLD
    assert doc["lineage"]["latest_source_at"] == _TS_NEW

    # Out-of-order arrival in between — extremes must not move.
    await repo.update_lineage(cid, source_txn_at=_TS_MID)
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["lineage"]["earliest_source_at"] == _TS_OLD
    assert doc["lineage"]["latest_source_at"] == _TS_NEW
    # ...but counter still advances.
    assert doc["lineage"]["source_transactions_count"] == 3


@pytest.mark.asyncio
async def test_update_lineage_respects_custom_txn_count(
    repo: FeatureRepository,
) -> None:
    """Passing ``txn_count=N`` increments by N (batched event flush)."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})
    await repo.update_lineage(cid, source_txn_at=_TS_OLD, txn_count=5)
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["lineage"]["source_transactions_count"] == 5


@pytest.mark.asyncio
async def test_update_lineage_no_match_returns_zero(
    repo: FeatureRepository,
) -> None:
    """``upsert=False`` — caller must have seeded the row first."""
    matched = await repo.update_lineage("cust_missing", source_txn_at=_TS_OLD)
    assert matched == 0


# --- write_quality ----------------------------------------------------


@pytest.mark.asyncio
async def test_write_quality_partial_patch(
    repo: FeatureRepository,
) -> None:
    """Only the kwargs supplied by the caller make it into the doc."""
    cid = "cust_r1"
    # Pre-seed both fields so we can assert the partial patch left
    # untouched fields alone.
    await repo.upsert(
        cid,
        {
            "customer_id": cid,
            "quality": {
                "missing_inputs": [],
                "outlier_flags": ["spike"],
                "confidence": 0.7,
            },
        },
    )
    matched = await repo.write_quality(cid, missing_inputs=["amount"])
    assert matched == 1
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["quality"]["missing_inputs"] == ["amount"]
    assert doc["quality"]["outlier_flags"] == ["spike"]
    assert doc["quality"]["confidence"] == 0.7


@pytest.mark.asyncio
async def test_write_quality_clamps_confidence(
    repo: FeatureRepository,
) -> None:
    """``confidence`` is clamped to [0.0, 1.0] on write."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})

    await repo.write_quality(cid, confidence=1.5)
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["quality"]["confidence"] == 1.0

    await repo.write_quality(cid, confidence=-0.2)
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["quality"]["confidence"] == 0.0

    # Mid-range value passes through verbatim.
    await repo.write_quality(cid, confidence=0.42)
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["quality"]["confidence"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_write_quality_noop_when_no_kwargs(
    repo: FeatureRepository,
) -> None:
    """Calling with no kwargs is a no-op; doesn't bump updated_at."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})
    matched = await repo.write_quality(cid)
    assert matched == 0


# --- get_with_lineage -------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_lineage_returns_subdocs(
    repo: FeatureRepository,
) -> None:
    """Both lineage and quality sub-docs must round-trip."""
    cid = "cust_r1"
    await repo.upsert(
        cid,
        {
            "customer_id": cid,
            "lineage": {
                "source_transactions_count": 3,
                "earliest_source_at": _TS_OLD,
                "latest_source_at": _TS_NEW,
            },
            "quality": {
                "missing_inputs": ["package_value_myr"],
                "outlier_flags": [],
                "confidence": 0.85,
            },
        },
    )
    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["customer_id"] == cid
    assert doc["lineage"]["source_transactions_count"] == 3
    assert doc["lineage"]["earliest_source_at"] == _TS_OLD
    assert doc["lineage"]["latest_source_at"] == _TS_NEW
    assert doc["quality"]["missing_inputs"] == ["package_value_myr"]
    assert doc["quality"]["confidence"] == 0.85

    # Negative case: missing customer → None.
    assert await repo.get_with_lineage("cust_missing") is None


@pytest.mark.asyncio
async def test_lineage_then_quality_round_trip(
    repo: FeatureRepository,
) -> None:
    """Smoke: a realistic worker flow — seed, update lineage twice,
    patch quality, then fetch — yields a coherent merged document."""
    cid = "cust_r1"
    await repo.upsert(cid, {"customer_id": cid})

    await repo.update_lineage(cid, source_txn_at=_TS_OLD)
    await repo.update_lineage(
        cid, source_txn_at=_TS_OLD + timedelta(hours=2)
    )
    await repo.write_quality(
        cid, missing_inputs=[], outlier_flags=["zscore_5"], confidence=0.9
    )

    doc = await repo.get_with_lineage(cid)
    assert doc is not None
    assert doc["lineage"]["source_transactions_count"] == 2
    assert doc["quality"]["outlier_flags"] == ["zscore_5"]
    assert doc["quality"]["confidence"] == 0.9
