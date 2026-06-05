"""Unit tests for `app.workers.feature_engineer`.

Covers PR-10 changes:
  * `_derive_feature_writes` — pure derivation function used to build
    the features-collection update spec from a transaction document.
  * Lineage tracking on every features write.
  * Skipping the second update when `computed_signals.txn_velocity_5m`
    is provided by the producer (V3 extended-reference shape from PR-3).
  * `_sync_customer_embeds` mirrors the new fields onto
    `customers.latest_features`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workers.feature_engineer import (
    FeatureEngineer,
    _LATEST_FEATURES_FIELDS,
    _derive_feature_writes,
)
from tests._fakes import FakeDB


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_txn(
    *,
    customer_id: str = "c-1",
    amount: float = 100.0,
    discount_amount: float = 5.0,
    timestamp: datetime | None = None,
    computed_signals: dict | None = None,
) -> dict[str, Any]:
    ts = timestamp or datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    txn: dict[str, Any] = {
        "transaction_id": "t-1",
        "customer_id": customer_id,
        "amount": amount,
        "discount_amount": discount_amount,
        "timestamp": ts,
        "transaction_type": "ppv_purchase",
    }
    if computed_signals is not None:
        txn["computed_signals"] = computed_signals
    return txn


# ----------------------------------------------------------------------
# 1-5: pure `_derive_feature_writes` cases
# ----------------------------------------------------------------------


def test_derive_writes_does_not_touch_5m_window_fields() -> None:
    """The 5-min hopping window is owned by ASP acme-feature-window-5m;
    the FE worker must never write `txn_count_5m` / `amount_sum_5m` /
    `discount_sum_5m`, even when `computed_signals.txn_velocity_5m` is
    populated by the producer (the producer-side hint is now stamped
    onto the source txn, not the features doc)."""
    txn = _make_txn(
        computed_signals={
            "txn_velocity_5m": 12,
            "amount_vs_avg_30d_pct": 42.0,
        }
    )
    spec = _derive_feature_writes(txn)

    assert "txn_count_5m" not in spec["set"]
    assert "amount_sum_5m" not in spec["set"]
    assert "discount_sum_5m" not in spec["set"]
    # Lineage and pull-through still happen.
    assert spec["set"]["amount_vs_avg_30d_pct"] == 42.0
    assert spec["use_signals"]["amount_vs_avg_30d_pct"] is True


def test_derive_writes_pulls_signals_when_present() -> None:
    """`amount_vs_avg_30d_pct` and friends are pulled from
    `computed_signals` onto the features doc."""
    txn = _make_txn(
        computed_signals={
            "amount_vs_avg_30d_pct": 17.5,
            "discount_pct_vs_avg_30d": 0.12,
        },
    )
    spec = _derive_feature_writes(txn)

    assert spec["set"]["amount_vs_avg_30d_pct"] == 17.5
    assert spec["set"]["discount_pct_vs_avg_30d"] == 0.12
    assert "txn_count_5m" not in spec["set"]


def test_derive_writes_no_signals_yields_pure_lineage_update() -> None:
    """Without `computed_signals`, the spec carries lineage / counters /
    last_txn_at only — no 5m-window fields, no $push, no second update."""
    txn = _make_txn()
    spec = _derive_feature_writes(txn)

    assert "txn_count_5m" not in spec["set"]
    assert "push" not in spec
    assert "skip_window_recompute" not in spec
    assert spec["inc"]["txn_count_total"] == 1
    assert spec["inc"]["lineage.source_transactions_count"] == 1


def test_derive_writes_lineage_tracking() -> None:
    """Every write must bump `lineage.source_transactions_count` and
    track earliest/latest source timestamps."""
    ts = datetime(2026, 5, 7, 9, 30, tzinfo=timezone.utc)
    txn = _make_txn(timestamp=ts)
    spec = _derive_feature_writes(txn)

    assert spec["inc"]["lineage.source_transactions_count"] == 1
    assert spec["min"]["lineage.earliest_source_at"] == ts
    assert spec["max"]["lineage.latest_source_at"] == ts


def test_derive_writes_high_risk_charge_code_flag() -> None:
    txn = _make_txn(
        computed_signals={"is_high_risk_charge_code": True},
    )
    spec = _derive_feature_writes(txn)

    assert spec["set"]["is_high_risk_charge_code"] is True
    assert spec["use_signals"]["is_high_risk_charge_code"] is True


# ----------------------------------------------------------------------
# 6-7: `_update_features` round-trip count
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engineer_issues_one_update_regardless_of_signals() -> None:
    """ASP owns the 5m hopping window; the FE worker now always issues
    exactly one update per txn change-stream event — no prune+recompute
    second round-trip, no signals-vs-no-signals branching."""
    db = FakeDB()
    feat = db["features"]
    feat._docs.append({  # type: ignore[attr-defined]
        "customer_id": "c-1",
        "txn_count_total": 0,
        "lineage": {"source_transactions_count": 0},
    })

    real_update_one = feat.update_one
    calls: list[tuple] = []

    async def _counting_update_one(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_update_one(*args, **kwargs)

    feat.update_one = _counting_update_one  # type: ignore[assignment]

    txn = _make_txn(computed_signals={"txn_velocity_5m": 7})
    fe = FeatureEngineer()
    await fe._update_features(feat, txn)

    assert len(calls) == 1
    saved = await real_update_one.__self__.find_one({"customer_id": "c-1"})
    # FE worker no longer writes the 5m fields — ASP does.
    assert "txn_count_5m" not in saved or saved.get("txn_count_5m") is None
    # Lineage made it through.
    assert saved["lineage"]["source_transactions_count"] == 1
    assert saved["lineage"]["earliest_source_at"] == txn["timestamp"]
    assert saved["lineage"]["latest_source_at"] == txn["timestamp"]


@pytest.mark.asyncio
async def test_engineer_no_signals_still_one_update() -> None:
    """No computed_signals → still exactly one update_one call. The
    legacy two-update prune+recompute path is gone."""
    db = FakeDB()
    feat = db["features"]

    real_update_one = feat.update_one
    calls: list[tuple] = []

    async def _counting_update_one(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_update_one(*args, **kwargs)

    feat.update_one = _counting_update_one  # type: ignore[assignment]

    txn = _make_txn()
    fe = FeatureEngineer()
    await fe._update_features(feat, txn)

    assert len(calls) == 1


# ----------------------------------------------------------------------
# 8: customers.latest_features snapshot includes new fields
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_features_snapshot_updated_on_customer() -> None:
    """After running `_update_features` then `_sync_customer_embeds`,
    the FE-worker-pulled fields (`amount_vs_avg_30d_pct`,
    `is_high_risk_charge_code`) land in `customers.latest_features`.
    The 5m-window field (`txn_count_5m`) is mirrored only if ASP /
    the seeder has populated it on the features doc — the FE worker
    no longer writes it."""
    db = FakeDB()
    feat = db["features"]
    customers = db["customers_residential"]

    # Customer must exist for `_sync_customer_embeds` to update it.
    customers._docs.append({  # type: ignore[attr-defined]
        "customer_id": "c-1",
        "recent_transactions": [],
    })
    # Simulate ASP acme-feature-window-5m having already written the
    # 5m hopping-window fields onto the features doc.
    feat._docs.append({  # type: ignore[attr-defined]
        "customer_id": "c-1",
        "txn_count_5m": 4,
    })

    txn = _make_txn(
        computed_signals={
            "amount_vs_avg_30d_pct": 33.3,
            "is_high_risk_charge_code": True,
        }
    )
    fe = FeatureEngineer()
    await fe._update_features(feat, txn)
    await fe._sync_customer_embeds(customers, feat, txn)

    saved = await customers.find_one({"customer_id": "c-1"})
    snapshot = saved["latest_features"]
    assert snapshot["amount_vs_avg_30d_pct"] == 33.3
    assert snapshot["txn_count_5m"] == 4  # mirrored from ASP-written features doc
    assert snapshot["is_high_risk_charge_code"] is True
    assert "as_of" in snapshot


# ----------------------------------------------------------------------
# Sanity: PR-10 `_LATEST_FEATURES_FIELDS` extensions
# ----------------------------------------------------------------------


def test_latest_features_fields_includes_pr10_additions() -> None:
    for field in (
        "amount_vs_avg_30d_pct",
        "discount_pct_vs_avg_30d",
        "is_high_risk_charge_code",
        "txn_count_5m",
    ):
        assert field in _LATEST_FEATURES_FIELDS, field
