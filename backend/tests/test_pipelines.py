"""Pipeline-builder unit tests — V3 extended-reference shape (PR-6).

These exercise the per-rule_type aggregation builders + the customer_360
pipeline shape, without needing MongoDB. They lock in:
  - the right field paths (V3 extended-reference, no $lookup),
  - parameters flow into the $match,
  - the dispatch table covers every rule_type,
  - the CI invariant that no rule pipeline contains a $lookup stage.
"""

from __future__ import annotations

import json

import pytest

from app.pipelines.customer_360_builder import customer_360_pipeline
from app.pipelines.rule_pipeline_builders import (
    RULE_BUILDERS,
    build_rule_pipeline,
)


# --- top-level invariants ------------------------------------------

def test_customer_360_pipeline_is_single_doc_fetch() -> None:
    """ADR-011: Customer 360 reads are a single document fetch — no $lookup,
    every dependency is embedded on the customer doc.
    """
    pipe = customer_360_pipeline("cust_000001")
    assert pipe == [
        {"$match": {"customer_id": "cust_000001"}},
        {"$project": {"_id": 0}},
    ]
    assert not any("$lookup" in s for s in pipe)
    assert not any("$unwind" in s for s in pipe)


def test_rule_builders_cover_all_known_types() -> None:
    expected = {
        "discount_mismatch", "velocity_anomaly", "amount_outlier",
        "entitlement_mismatch", "geographic_anomaly", "duplicate_transaction",
        # Phase B.2 — Acme-named rules
        "termination_fee_check", "unearned_earned_segregation",
        "double_charge_multi_code", "proration_check",
    }
    assert set(RULE_BUILDERS) == expected


def test_no_lookup_anywhere_in_any_rule_pipeline() -> None:
    """ADR-025 / PR-6 CI assertion: rule pipelines must read only from the
    transaction document's extended-reference fields. Any `$lookup` stage
    would re-introduce a join at evaluation time and break the
    constant-latency promise.
    """
    for rule_type, builder in RULE_BUILDERS.items():
        pipeline = builder({})
        rendered = json.dumps(pipeline, default=str)
        assert "$lookup" not in rendered, (
            f"rule {rule_type!r} pipeline contains a $lookup stage; "
            "rewrite to read from extended-reference fields only"
        )


def test_dispatch_unknown_rule_type_raises() -> None:
    with pytest.raises(KeyError):
        build_rule_pipeline("nonsense_rule", {})


# --- legacy-name builders (rewritten to V3) ------------------------

def test_discount_mismatch_uses_v3_extref_paths() -> None:
    pipe = build_rule_pipeline("discount_mismatch", {"min_discount_amount_myr": 12.5})
    first_match = pipe[0]
    assert first_match["$match"]["total_discount_myr"] == {"$gt": 12.5}
    # Reads from frozen customer_summary, not via $lookup.
    rendered = json.dumps(pipe, default=str)
    assert "customer_summary.active_promotions_at_billing" in rendered
    assert "$lookup" not in rendered


def test_velocity_anomaly_groups_by_keys_and_sums_total_myr() -> None:
    pipe = build_rule_pipeline(
        "velocity_anomaly",
        {"window_seconds": 60, "max_transactions": 3, "group_by": ["customer_id"]},
    )
    grp = next(s for s in pipe if "$group" in s)
    assert grp["$group"]["_id"] == {"customer_id": "$customer_id"}
    # V3: aggregate `total_myr`, not legacy `amount`.
    assert grp["$group"]["total_myr"] == {"$sum": "$total_myr"}
    final_match = [s for s in pipe if "$match" in s][-1]
    assert final_match == {"$match": {"txn_count": {"$gt": 3}}}


def test_amount_outlier_partitions_on_total_myr() -> None:
    pipe = build_rule_pipeline(
        "amount_outlier",
        {"std_dev_multiplier": 2.5, "lookback_days": 30, "minimum_history_count": 5},
    )
    swf = next(s for s in pipe if "$setWindowFields" in s)
    output = swf["$setWindowFields"]["output"]
    assert output["_avg"]["$avg"] == "$total_myr"
    assert output["_stddev"]["$stdDevSamp"] == "$total_myr"


def test_entitlement_mismatch_reads_summary_and_items() -> None:
    pipe = build_rule_pipeline("entitlement_mismatch", {})
    rendered = json.dumps(pipe, default=str)
    # No $lookup; reads from frozen entitlements + items.product_ref.
    assert "$lookup" not in rendered
    assert "customer_summary.active_entitlements_at_billing" in rendered
    assert "items.product_ref" in rendered


def test_geographic_anomaly_compares_to_summary_service_state() -> None:
    pipe = build_rule_pipeline("geographic_anomaly", {})
    rendered = json.dumps(pipe, default=str)
    assert "$lookup" not in rendered
    # The expr compares the txn location to the summary's service_state.
    assert "customer_summary.service_state" in rendered
    assert "location.state" in rendered


def test_duplicate_transaction_uses_total_myr_and_window() -> None:
    pipe = build_rule_pipeline(
        "duplicate_transaction",
        {"window_seconds": 30, "fields_to_match": ["customer_id", "total_myr"]},
    )
    grp = next(s for s in pipe if "$group" in s)
    assert grp["$group"]["_id"] == {"customer_id": "$customer_id", "total_myr": "$total_myr"}


# --- Phase B.2 — Acme-named rule builders (V3 paths) ----------------

def test_termination_fee_check_matches_items_charge_code() -> None:
    pipe = build_rule_pipeline(
        "termination_fee_check",
        {"charge_code": "CC_TERMINATION_FEE", "min_amount_myr": 100.0},
    )
    first_match = pipe[0]
    assert first_match == {"$match": {
        "items.charge_code": "CC_TERMINATION_FEE",
        "total_myr": {"$gte": 100.0},
    }}
    # V3 must reference the extended-reference signal; never $lookup.
    rendered = json.dumps(pipe, default=str)
    assert "$lookup" not in rendered
    assert "computed_signals.is_lock_in_period" in rendered


def test_unearned_earned_segregation_uses_first_item_metadata() -> None:
    pipe = build_rule_pipeline(
        "unearned_earned_segregation",
        {"applies_to_types": ["subscription_charge"], "tolerance_myr": 0.01},
    )
    first_match = pipe[0]
    assert first_match == {"$match": {
        "transaction_type": {"$in": ["subscription_charge"]},
    }}
    rendered = json.dumps(pipe, default=str)
    # First-item slicing → no $lookup.
    assert "$lookup" not in rendered
    assert "$arrayElemAt" in rendered
    # Tolerance is plumbed through.
    assert any(0.01 == v for v in _all_numbers(pipe))


def test_double_charge_multi_code_unwinds_items() -> None:
    pipe = build_rule_pipeline(
        "double_charge_multi_code",
        {"redundant_codes": ["CC_X", "CC_Y"],
         "match_fields": ["customer_id", "items.product_ref"],
         "window_seconds": 300},
    )
    # $unwind on items lets us inspect per-line charge_code without $lookup.
    unwind = next(s for s in pipe if "$unwind" in s)
    assert unwind["$unwind"]["path"] == "$items"
    code_match = next(
        s for s in pipe
        if "$match" in s and "items.charge_code" in s.get("$match", {})
    )
    assert code_match["$match"]["items.charge_code"] == {"$in": ["CC_X", "CC_Y"]}
    grp = next(s for s in pipe if "$group" in s)
    # match_fields land in the group _id with field-name-safe keys.
    assert grp["$group"]["_id"]["customer_id"] == "$customer_id"
    assert grp["$group"]["_id"]["item_product_ref"] == "$items.product_ref"


def test_proration_check_matches_first_item_mid_cycle_flag() -> None:
    pipe = build_rule_pipeline(
        "proration_check",
        {"applies_to_types": ["subscription_charge"], "tolerance_pct": 0.10},
    )
    first_match = pipe[0]
    assert first_match == {"$match": {
        "transaction_type": {"$in": ["subscription_charge"]},
    }}
    # The mid-cycle flag is matched on the first item's metadata after
    # an $arrayElemAt projection.
    rendered = json.dumps(pipe, default=str)
    assert "$lookup" not in rendered
    assert "_first_item_meta.is_mid_cycle_change" in rendered
    assert any(0.10 == v for v in _all_numbers(pipe))


def _all_numbers(obj):
    """Yield every numeric leaf in a nested aggregation pipeline."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_numbers(v)
