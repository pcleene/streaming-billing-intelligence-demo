"""Unit tests for `scripts/seed_features.py` (PR-14).

Drives the seed entry point against a FakeDB; no live Mongo required.
After the canonical-shape refactor, every doc must carry every key
from `CANONICAL_FEATURE_FIELDS` at the top level (no nested
`features.{...}` wrapper) and use the PR-10 lineage / quality contract.
"""

from __future__ import annotations

import pytest

from app.core.constants import CUSTOMERS_RESIDENTIAL, FEATURES
from app.schemas.feature import CANONICAL_FEATURE_FIELDS
from scripts.seed_features import (
    FEATURE_SET_VERSION,
    SEED_MARKER,
    TRACKED_FEATURES,
    main as seed_features_main,
)
from tests._fakes import FakeDB


def _seed_real_customers(db: FakeDB, n: int) -> None:
    for i in range(n):
        db[CUSTOMERS_RESIDENTIAL]._docs.append({"customer_id": f"cust_{i:04d}"})


@pytest.mark.asyncio
async def test_seed_features_inserts_requested_count_with_synthetic_topup() -> None:
    """Empty residential collection → synthetic ids fill the count slot."""
    db = FakeDB()
    result = await seed_features_main(db, count=12)

    assert result["inserted"] == 12
    assert result["customer_count"] == 12
    assert result["marker"] == SEED_MARKER
    docs = db[FEATURES]._docs
    assert len(docs) == 12


@pytest.mark.asyncio
async def test_seed_features_uses_real_customer_ids_when_present() -> None:
    db = FakeDB()
    _seed_real_customers(db, 5)
    result = await seed_features_main(db, count=10)

    assert result["inserted"] == 10
    docs = db[FEATURES]._docs
    cids = {d["customer_id"] for d in docs}
    # The first 5 should be the real customers.
    for i in range(5):
        assert f"cust_{i:04d}" in cids


@pytest.mark.asyncio
async def test_seed_features_is_idempotent_under_marker_wipe() -> None:
    """Running twice keeps the doc count stable."""
    db = FakeDB()
    first = await seed_features_main(db, count=8)
    second = await seed_features_main(db, count=8)

    assert first["inserted"] == 8
    assert second["inserted"] == 8
    docs = db[FEATURES]._docs
    assert len(docs) == 8
    # All carry the marker.
    assert all(d.get("_seed_marker") == SEED_MARKER for d in docs)


@pytest.mark.asyncio
async def test_seed_features_doc_shape_is_flat_canonical() -> None:
    """Every features doc must carry every canonical field at the top level.

    This is the canonical-shape promise: no nested `features.{...}`
    wrapper, no missing fields. Drift detector and iforest scorer both
    read top-level — the seeder must match.
    """
    db = FakeDB()
    await seed_features_main(db, count=15)
    docs = db[FEATURES]._docs

    for d in docs:
        # No nested wrapper anymore.
        assert "features" not in d, (
            "seeder must not produce a nested `features` sub-doc"
        )
        # Every canonical field is present at the top level.
        missing = [k for k in CANONICAL_FEATURE_FIELDS if k not in d]
        assert not missing, f"missing canonical fields: {missing}"
        # TRACKED_FEATURES (drift detector inputs) all live top-level.
        for key in TRACKED_FEATURES:
            assert key in d, f"{key} missing from top-level"


@pytest.mark.asyncio
async def test_seed_features_feature_set_version_pinned() -> None:
    db = FakeDB()
    await seed_features_main(db, count=6)
    for d in db[FEATURES]._docs:
        assert d.get("feature_set_version") == FEATURE_SET_VERSION
        assert FEATURE_SET_VERSION == "v3.2.1"


@pytest.mark.asyncio
async def test_seed_features_quality_subdoc_in_pr10_shape() -> None:
    db = FakeDB()
    await seed_features_main(db, count=20)
    for d in db[FEATURES]._docs:
        q = d.get("quality") or {}
        # PR-10 contract fields all present.
        assert "missing_inputs" in q
        assert "outlier_flags" in q
        assert "confidence" in q
        assert "computed_via" in q
        # Origin tag identifies seeded data.
        assert q["computed_via"] == "seed"
        # Empty arrays for the demo seeder.
        assert q["missing_inputs"] == []
        assert q["outlier_flags"] == []
        # Low-confidence — these are warm-start placeholders.
        conf = q["confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0


@pytest.mark.asyncio
async def test_seed_features_lineage_subdoc_in_pr10_shape() -> None:
    """Seeder must use the canonical lineage field names that the FE
    worker, FeatureRepository, and PR-10 contract all agree on.
    """
    db = FakeDB()
    await seed_features_main(db, count=4)
    for d in db[FEATURES]._docs:
        lin = d.get("lineage") or {}
        assert "source_transactions_count" in lin
        assert "earliest_source_at" in lin
        assert "latest_source_at" in lin
        # Old shape must be gone.
        assert "txn_count" not in lin
        assert "first_txn_at" not in lin
        assert "last_txn_at" not in lin
        assert "last_recomputed_at" not in lin


@pytest.mark.asyncio
async def test_seed_features_iforest_input_columns_present() -> None:
    """The 9 columns the iforest model is trained on must be present
    (even when None for Spark-owned 30d/1h windows) — otherwise the
    scoring path can't even build a feature row.
    """
    iforest_columns = (
        "txn_count_1h",
        "txn_count_24h",
        "txn_count_7d",
        "spend_24h_myr",
        "spend_7d_myr",
        "discount_rate_30d",
        "quarantine_count_30d",
        "package_value_myr",
        "spend_to_package_ratio",
    )
    db = FakeDB()
    await seed_features_main(db, count=10)
    for d in db[FEATURES]._docs:
        for col in iforest_columns:
            assert col in d, f"iforest input {col} missing from canonical doc"
