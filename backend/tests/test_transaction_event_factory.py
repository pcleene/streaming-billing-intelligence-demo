"""PR-3 follow-up — V3 transaction event factory unit tests.

Asserts:
  * the cumulative-threshold distribution roughly matches
    `DISTRIBUTION_TARGETS` over a large sample (seeded `random.Random`,
    so the tolerance can be tight)
  * each `force_type` produces the canonical line item (correct
    `charge_code`, item_type, V3 fields)
  * commercial customers get `CC_BIZ_SPORTS_PER_OUTLET` on the
    subscription line and never receive PPV
  * anomaly flavours produce expected drift (cross-state, oversized
    amount, applied discount)
  * top-level event shape contains the V3 fields the repo expects
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timezone

from app.workers.transaction_event_factory import (
    DISTRIBUTION_TARGETS,
    STATES,
    build_v3_event_payload,
)

_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _residential() -> dict:
    return {
        "customer_id": "cust_r1",
        "customer_type": "residential",
        "address": {"state": "Selangor"},
        "subscriptions": [{
            "package_name": "Premium",
            "status": "active",
            "monthly_fee_myr": 199.0,
        }],
        "early_termination_fee_myr": 240.0,
    }


def _commercial() -> dict:
    return {
        "customer_id": "cust_C1",
        "customer_type": "commercial",
        "address": {"state": "Kuala Lumpur"},
        "subscriptions": [{
            "package_name": "Sports Bundle",
            "status": "active",
            "monthly_fee_myr": 500.0,
        }],
    }


# --- distribution -----------------------------------------------------


def test_distribution_matches_master_plan_within_tolerance() -> None:
    rng = random.Random(20260507)
    customer = _residential()
    counts: Counter[str] = Counter()
    n = 20_000
    for _ in range(n):
        ev = build_v3_event_payload(customer, ts=_NOW, rng=rng)
        counts[ev["transaction_type"]] += 1

    for txn_type, target in DISTRIBUTION_TARGETS.items():
        observed = counts[txn_type] / n
        assert abs(observed - target) < 0.02, (
            f"{txn_type}: observed {observed:.3f} vs target {target:.3f}"
        )


def test_commercial_customers_skip_ppv() -> None:
    rng = random.Random(42)
    customer = _commercial()
    types: Counter[str] = Counter()
    for _ in range(2000):
        ev = build_v3_event_payload(customer, ts=_NOW, rng=rng)
        types[ev["transaction_type"]] += 1
    assert types["ppv_charge"] == 0


# --- per-variant shape ------------------------------------------------


def test_force_type_subscription_charge_residential() -> None:
    rng = random.Random(1)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="subscription_charge"
    )
    assert ev["transaction_type"] == "subscription_charge"
    assert ev["channel"] == "auto_debit"
    assert ev["payment"]["auto_debit"] is True
    item = ev["items"][0]
    assert item["item_type"] == "subscription_charge"
    assert item["charge_code"] == "CC_SUBSCRIPTION_BASE"
    assert item["unit_price_myr"] == 199.0
    assert item["line_total_myr"] == 199.0


def test_force_type_subscription_charge_commercial_uses_per_outlet_code() -> None:
    rng = random.Random(2)
    ev = build_v3_event_payload(
        _commercial(), ts=_NOW, rng=rng, force_type="subscription_charge"
    )
    item = ev["items"][0]
    assert item["charge_code"] == "CC_BIZ_SPORTS_PER_OUTLET"


def test_force_type_ppv_charge_shape() -> None:
    rng = random.Random(3)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="ppv_charge"
    )
    item = ev["items"][0]
    assert item["item_type"] == "ppv_charge"
    assert item["charge_code"] == "CC_PPV_DIRECT"
    assert 9.90 <= item["unit_price_myr"] <= 24.90


def test_force_type_refund_negative_total() -> None:
    rng = random.Random(4)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="refund"
    )
    item = ev["items"][0]
    assert item["item_type"] == "refund"
    assert item["charge_code"] == "CC_REFUND"
    assert item["unit_price_myr"] < 0
    assert item["line_total_myr"] < 0
    assert item["refund_reference_transaction_id"].startswith("txn_orig_")
    assert item["refund_reason"] in {
        "service_outage", "billing_error", "good_will", "double_charge"
    }


def test_force_type_termination_fee_carries_expected_delta() -> None:
    rng = random.Random(5)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="termination_fee"
    )
    item = ev["items"][0]
    assert item["item_type"] == "termination_fee"
    assert item["charge_code"] == "CC_TERMINATION_FEE"
    assert item["computed_expected_fee_myr"] == 240.0
    assert 192.0 <= item["unit_price_myr"] <= 288.0
    assert item["expected_vs_billed_delta_myr"] == round(
        item["unit_price_myr"] - 240.0, 2
    )


def test_force_type_device_fee_shape() -> None:
    rng = random.Random(6)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="device_fee"
    )
    item = ev["items"][0]
    assert item["item_type"] == "device_fee"
    assert item["charge_code"] == "CC_DEVICE_FEE"
    assert 15.0 <= item["unit_price_myr"] <= 60.0


# --- top-level event shape -------------------------------------------


def test_event_top_level_shape_has_v3_fields() -> None:
    rng = random.Random(7)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="subscription_charge"
    )
    for key in (
        "transaction_id", "customer_id", "timestamp", "transaction_type",
        "merchant_id", "channel", "currency", "location", "items",
        "discounts", "tax", "payment", "metadata",
    ):
        assert key in ev, f"missing top-level field: {key}"
    assert ev["currency"] == "MYR"
    assert ev["location"]["country"] == "MY"
    assert ev["tax"]["rate"] == 0.06
    assert ev["tax"]["amount_myr"] == 11.94
    # Repo recomputes totals; factory must not pre-stamp them.
    assert "subtotal_myr" not in ev
    assert "total_myr" not in ev


def test_non_subscription_uses_self_serve_channel_and_credit_card() -> None:
    rng = random.Random(8)
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, force_type="ppv_charge"
    )
    assert ev["channel"] == "self_serve"
    assert ev["payment"]["method"] == "credit_card"
    assert ev["payment"]["auto_debit"] is False


# --- anomaly flavours ------------------------------------------------


class _FlavourPinnedRng(random.Random):
    """Force `rng.choice(["geo","discount","huge_amount"])` to a fixed value
    while leaving every other rng call to the underlying Random."""

    def __init__(self, flavour: str, seed: int = 0) -> None:
        super().__init__(seed)
        self._flavour = flavour

    def choice(self, seq):  # type: ignore[override]
        if list(seq) == ["geo", "discount", "huge_amount"]:
            return self._flavour
        return super().choice(seq)


def test_anomaly_geo_picks_a_different_state() -> None:
    customer = _residential()
    home = customer["address"]["state"]
    rng = _FlavourPinnedRng("geo")
    ev = build_v3_event_payload(
        customer, ts=_NOW, rng=rng, anomaly=True,
        force_type="subscription_charge",
    )
    assert ev["location"]["state"] != home
    assert ev["location"]["state"] in STATES


def test_anomaly_discount_applies_when_subscription() -> None:
    rng = _FlavourPinnedRng("discount")
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, anomaly=True,
        force_type="subscription_charge",
    )
    item = ev["items"][0]
    assert item["discount_per_unit_myr"] > 0.0
    assert item["line_total_myr"] < 199.0
    assert ev["discounts"] and ev["discounts"][0]["promo_id"] == "PROMO_ANOMALY"


def test_anomaly_huge_amount_blows_up_unit_price() -> None:
    rng = _FlavourPinnedRng("huge_amount")
    ev = build_v3_event_payload(
        _residential(), ts=_NOW, rng=rng, anomaly=True,
        force_type="subscription_charge",
    )
    item = ev["items"][0]
    assert 2000.0 <= item["unit_price_myr"] <= 5000.0
    assert item["line_total_myr"] == item["unit_price_myr"]


def test_default_rng_path_executes() -> None:
    # When rng is omitted we fall back to the global random module —
    # ensure the path runs and emits a sensible event.
    ev = build_v3_event_payload(_residential(), ts=_NOW)
    assert ev["transaction_id"].startswith("txn_")
    assert ev["items"], "should always emit at least one line item"
