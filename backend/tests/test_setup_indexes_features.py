"""PR-10 — assert that the feature-store compound indexes are
registered in :mod:`scripts.setup_indexes`.

Both indexes are keyed off the recent-write access patterns of the
feature engineer (``customer_id, as_of DESC``) and the feature drift
detector (``feature_name, measured_at DESC``). No live Mongo needed —
we read the module-level ``REGULAR_INDEXES`` table.
"""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from app.core.constants import FEATURE_DRIFT_METRICS, FEATURES
from scripts.setup_indexes import REGULAR_INDEXES


def _has_index(coll: str, expected_keys: list[tuple[str, int]]) -> bool:
    """True iff ``REGULAR_INDEXES[coll]`` contains an entry whose key
    list matches ``expected_keys`` exactly (order-sensitive — compound
    indexes are key-order-sensitive in MongoDB)."""
    for keys, _opts in REGULAR_INDEXES.get(coll, []):
        if list(keys) == list(expected_keys):
            return True
    return False


def test_features_index_registered():
    """`features` must have a compound index on (customer_id ASC, as_of DESC)
    so the engineer's "latest features for this customer" query is
    point-lookup fast."""
    assert _has_index(
        FEATURES,
        [("customer_id", ASCENDING), ("as_of", DESCENDING)],
    ), (
        "REGULAR_INDEXES[FEATURES] must contain a compound index "
        "(customer_id ASC, as_of DESC)"
    )


def test_feature_drift_metrics_index_registered():
    """`feature_drift_metrics` must have (feature_name ASC, measured_at DESC)
    so the dashboard's per-feature severity timeline is a single
    index-only scan."""
    assert _has_index(
        FEATURE_DRIFT_METRICS,
        [("feature_name", ASCENDING), ("measured_at", DESCENDING)],
    ), (
        "REGULAR_INDEXES[FEATURE_DRIFT_METRICS] must contain a compound "
        "index (feature_name ASC, measured_at DESC)"
    )


def test_features_index_uses_descending_as_of():
    """Defensive: the as_of leg MUST be descending so reverse-scan
    queries hit the index forward instead of falling back to an
    in-memory sort."""
    entries = REGULAR_INDEXES[FEATURES]
    matching = [
        keys for keys, _ in entries
        if any(k == "as_of" for k, _d in keys)
    ]
    assert matching, "no FEATURES index references as_of"
    for keys in matching:
        for k, direction in keys:
            if k == "as_of":
                assert direction == DESCENDING, (
                    f"as_of leg must be DESCENDING, got {direction}"
                )


def test_drift_metrics_index_uses_descending_measured_at():
    """Same defensive check on the drift-series side."""
    entries = REGULAR_INDEXES[FEATURE_DRIFT_METRICS]
    matching = [
        keys for keys, _ in entries
        if any(k == "measured_at" for k, _d in keys)
    ]
    assert matching, "no FEATURE_DRIFT_METRICS index references measured_at"
    # At least one entry must pair (feature_name, measured_at DESC).
    found = False
    for keys in matching:
        names = [k for k, _d in keys]
        if names == ["feature_name", "measured_at"]:
            assert keys[1][1] == DESCENDING
            found = True
    assert found, (
        "expected a (feature_name, measured_at) compound index with "
        "measured_at DESCENDING"
    )
