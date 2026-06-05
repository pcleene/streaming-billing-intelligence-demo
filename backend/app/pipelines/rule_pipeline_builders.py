"""Per-rule_type aggregation builders — V3 extended-reference shape.

PR-6 rewrites every builder to read directly from the V3 transaction
document. There is **no `$lookup`** in any builder: customer state is
projected onto `customer_summary` at write time (PR-3), cycle anchors
are frozen on `bill_period` / `cycle_id`, charge codes are validated
against the in-memory catalog (PR-5), and per-line metadata travels on
`items[]`.

The CI assertion in `tests/test_pipelines.py::test_no_lookup_anywhere`
guards this contract.

Field-path migration (V2 → V3):
  - `amount`                     → `total_myr`
  - `discount_amount`            → `total_discount_myr`
  - `metadata.charge_code`       → `items.charge_code`
  - `metadata.earned_amount_myr` → `items.metadata.earned_amount_myr` (per-item)
  - `metadata.is_mid_cycle_change` → `items.metadata.is_mid_cycle_change`
  - `_customer.active_promotions` → `customer_summary.active_promotions_at_billing`
  - `_customer.entitlements`     → `customer_summary.active_entitlements_at_billing`
  - `_customer.address.state`    → `customer_summary.service_state`
  - `_customer.subscription_status` (proxy) → `computed_signals.is_lock_in_period`
  - `content_id`                 → `items.product_ref`

Each builder takes the rule's `parameters` dict and returns a list of
aggregation stages. Returned stages assume a `$match` filter limiting
the input set has already been applied OR include their own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

# A builder is a function(parameters_dict) -> list[stage]
RuleBuilder = Callable[[dict], list[dict]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- discount_mismatch -------------------------------------------------
def build_discount_mismatch(params: dict) -> list[dict]:
    """Discount applied but customer had no active promotion at billing.

    Reads `customer_summary.active_promotions_at_billing` (PR-3 frozen
    projection) instead of $lookup'ing the customers collection.
    """
    min_amt = params.get("min_discount_amount_myr", 0.0)
    return [
        {"$match": {
            "total_discount_myr": {"$gt": min_amt},
            "$expr": {
                "$eq": [
                    {"$size": {"$ifNull": [
                        "$customer_summary.active_promotions_at_billing", []
                    ]}},
                    0,
                ]
            },
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "total_discount_myr": 1,
            "merchant_id": 1, "timestamp": 1, "cycle_id": 1,
            "rule_type": {"$literal": "discount_mismatch"},
            "rule_name": {"$literal": "Discount with no active promotion"},
        }},
    ]


# --- velocity_anomaly ------------------------------------------------
def build_velocity_anomaly(params: dict) -> list[dict]:
    window_seconds = params.get("window_seconds", 300)
    max_txns = params.get("max_transactions", 5)
    group_keys = params.get("group_by", ["customer_id", "merchant_id"])
    group_id = {k: f"${k}" for k in group_keys}
    cutoff = _utcnow() - timedelta(seconds=window_seconds)
    return [
        {"$match": {"timestamp": {"$gte": cutoff}}},
        {"$group": {
            "_id": group_id,
            "txn_count": {"$sum": 1},
            "first_at": {"$min": "$timestamp"},
            "last_at": {"$max": "$timestamp"},
            "txn_ids": {"$push": "$transaction_id"},
            "total_myr": {"$sum": "$total_myr"},
        }},
        {"$match": {"txn_count": {"$gt": max_txns}}},
        {"$project": {
            "_id": 0,
            "customer_id": "$_id.customer_id",
            "merchant_id": "$_id.merchant_id",
            "txn_count": 1, "first_at": 1, "last_at": 1,
            "txn_ids": 1, "total_myr": 1,
            "rule_type": {"$literal": "velocity_anomaly"},
            "rule_name": {"$literal": "Velocity anomaly"},
        }},
    ]


# --- amount_outlier --------------------------------------------------
def build_amount_outlier(params: dict) -> list[dict]:
    """Per-customer std-dev outlier on `total_myr`."""
    multiplier = params.get("std_dev_multiplier", 3.0)
    lookback_days = params.get("lookback_days", 90)
    minimum = params.get("minimum_history_count", 10)
    cutoff = _utcnow() - timedelta(days=lookback_days)
    return [
        {"$match": {"timestamp": {"$gte": cutoff}}},
        {"$setWindowFields": {
            "partitionBy": "$customer_id",
            "sortBy": {"timestamp": 1},
            "output": {
                "_avg": {"$avg": "$total_myr", "window": {"documents": ["unbounded", -1]}},
                "_stddev": {"$stdDevSamp": "$total_myr", "window": {"documents": ["unbounded", -1]}},
                "_count": {"$sum": 1, "window": {"documents": ["unbounded", -1]}},
            }
        }},
        {"$match": {
            "_count": {"$gte": minimum},
            "$expr": {
                "$gt": [
                    {"$abs": {"$subtract": ["$total_myr", "$_avg"]}},
                    {"$multiply": [multiplier, {"$ifNull": ["$_stddev", 0]}]}
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1,
            "merchant_id": 1, "_avg": 1, "_stddev": 1,
            "rule_type": {"$literal": "amount_outlier"},
            "rule_name": {"$literal": "Amount outlier vs customer history"},
        }},
    ]


# --- entitlement_mismatch -------------------------------------------
def build_entitlement_mismatch(params: dict) -> list[dict]:
    """PPV charged but `customer_summary.active_entitlements_at_billing`
    doesn't include the purchased content.

    `items.product_ref` carries the content identifier on V3.
    """
    return [
        {"$match": {
            "transaction_type": {"$in": ["ppv_charge", "ppv_purchase"]},
            "items.product_ref": {"$ne": None, "$exists": True},
        }},
        {"$addFields": {
            "_purchased_refs": {
                "$ifNull": ["$items.product_ref", []]
            },
            "_entitled": {
                "$ifNull": ["$customer_summary.active_entitlements_at_billing", []]
            },
        }},
        {"$match": {
            "$expr": {
                "$gt": [
                    {"$size": {"$setDifference": ["$_purchased_refs", "$_entitled"]}},
                    0,
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1, "cycle_id": 1,
            "purchased_refs": "$_purchased_refs",
            "entitled": "$_entitled",
            "rule_type": {"$literal": "entitlement_mismatch"},
            "rule_name": {"$literal": "PPV without entitlement"},
        }},
    ]


# --- geographic_anomaly ---------------------------------------------
def build_geographic_anomaly(params: dict) -> list[dict]:
    """Transaction `location.state` differs from
    `customer_summary.service_state` (frozen at billing time).
    """
    return [
        {"$match": {"location.state": {"$exists": True, "$ne": None}}},
        {"$match": {
            "$expr": {
                "$ne": ["$location.state", "$customer_summary.service_state"]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1,
            "txn_state": "$location.state",
            "home_state": "$customer_summary.service_state",
            "geographic_distance_from_home_km":
                "$computed_signals.geographic_distance_from_home_km",
            "rule_type": {"$literal": "geographic_anomaly"},
            "rule_name": {"$literal": "Geographic anomaly vs home state"},
        }},
    ]


# --- duplicate_transaction -----------------------------------------
def build_duplicate_transaction(params: dict) -> list[dict]:
    window_seconds = params.get("window_seconds", 60)
    fields = params.get("fields_to_match", ["customer_id", "merchant_id", "total_myr"])
    cutoff = _utcnow() - timedelta(seconds=max(window_seconds * 100, 3600))
    group_id = {f: f"${f}" for f in fields}
    return [
        {"$match": {"timestamp": {"$gte": cutoff}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": group_id,
            "dup_count": {"$sum": 1},
            "txn_ids": {"$push": "$transaction_id"},
            "first_at": {"$min": "$timestamp"},
            "last_at": {"$max": "$timestamp"},
        }},
        {"$match": {
            "dup_count": {"$gt": 1},
            "$expr": {
                "$lte": [
                    {"$dateDiff": {"startDate": "$first_at", "endDate": "$last_at",
                                   "unit": "second"}},
                    window_seconds,
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "customer_id": "$_id.customer_id",
            "merchant_id": "$_id.merchant_id",
            "total_myr": "$_id.total_myr",
            "dup_count": 1, "txn_ids": 1, "first_at": 1, "last_at": 1,
            "rule_type": {"$literal": "duplicate_transaction"},
            "rule_name": {"$literal": "Duplicate transactions"},
        }},
    ]


# --- termination_fee_check (Phase B.2, V3 rewrite) ------------------
def build_termination_fee_check(params: dict) -> list[dict]:
    """Termination fee charged while customer was in lock-in or the
    expected/billed delta exceeds tolerance.

    V3 reads only extended-reference fields:
      - `items.charge_code` for the fee identification,
      - `computed_signals.is_lock_in_period` for "still active" proxy
        (PR-3's `_compute_signals` sets this when
        `customer.lock_in_until > timestamp`),
      - `items.expected_vs_billed_delta_myr` for the fee delta tolerance.
    """
    charge_code = params.get("charge_code", "CC_TERMINATION_FEE")
    min_amt = params.get("min_amount_myr", 50.0)
    delta_tolerance = params.get("delta_tolerance_myr", 0.0)
    return [
        {"$match": {
            "items.charge_code": charge_code,
            "total_myr": {"$gte": min_amt},
        }},
        {"$match": {
            "$expr": {
                "$or": [
                    # Customer still in lock-in window when fee was charged.
                    {"$eq": ["$computed_signals.is_lock_in_period", True]},
                    # OR the billed amount diverges from the expected fee.
                    {"$gt": [
                        {"$abs": {"$ifNull": [
                            {"$arrayElemAt": [
                                "$items.expected_vs_billed_delta_myr", 0
                            ]},
                            0,
                        ]}},
                        delta_tolerance,
                    ]},
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1, "cycle_id": 1,
            "merchant_id": 1,
            "is_lock_in_period": "$computed_signals.is_lock_in_period",
            "rule_type": {"$literal": "termination_fee_check"},
            "rule_name": {
                "$literal": "Termination fee against lock-in or out-of-tolerance"
            },
        }},
    ]


# --- unearned_earned_segregation (Phase B.2, V3 rewrite) ------------
def build_unearned_earned_segregation(params: dict) -> list[dict]:
    """Subscription/addon items missing or mis-summing earned/unearned split.

    V3 stores per-item metadata on `items[].metadata`. We use
    `$arrayElemAt` over the items array — the rule applies to the first
    subscription/addon item (the canonical revenue-bearing line).
    """
    types = params.get("applies_to_types", ["subscription_charge", "addon_charge"])
    tolerance = params.get("tolerance_myr", 0.5)
    return [
        {"$match": {"transaction_type": {"$in": types}}},
        {"$addFields": {
            "_first_item":     {"$arrayElemAt": ["$items", 0]},
        }},
        {"$addFields": {
            "_first_item_meta": {"$ifNull": ["$_first_item.metadata", {}]},
            "_line_total":      {"$ifNull": ["$_first_item.line_total_myr", 0]},
        }},
        {"$addFields": {
            "_earned":   {"$ifNull": ["$_first_item_meta.earned_amount_myr", None]},
            "_unearned": {"$ifNull": ["$_first_item_meta.unearned_amount_myr", None]},
        }},
        {"$match": {
            "$expr": {
                "$or": [
                    {"$eq": ["$_earned", None]},
                    {"$eq": ["$_unearned", None]},
                    {"$gt": [
                        {"$abs": {"$subtract": [
                            {"$add": ["$_earned", "$_unearned"]},
                            "$_line_total",
                        ]}},
                        tolerance,
                    ]},
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1,
            "transaction_type": 1, "cycle_id": 1,
            "earned_amount_myr": "$_earned",
            "unearned_amount_myr": "$_unearned",
            "line_total_myr": "$_line_total",
            "rule_type": {"$literal": "unearned_earned_segregation"},
            "rule_name": {"$literal": "Earned/unearned split missing or out of tolerance"},
        }},
    ]


# --- double_charge_multi_code (Phase B.2, V3 rewrite) ---------------
def build_double_charge_multi_code(params: dict) -> list[dict]:
    """Same logical service charged via different charge_code values.

    V3 reads `items.charge_code` and `items.product_ref`. We unwind
    items to inspect each line's charge_code, then group on the
    configured match_fields (typically customer_id + product_ref).
    """
    redundant_codes = params.get("redundant_codes", ["CC_PPV_DIRECT", "CC_BUNDLE_PPV"])
    match_fields = params.get("match_fields", ["customer_id", "items.product_ref"])
    window_seconds = params.get("window_seconds", 600)
    cutoff = _utcnow() - timedelta(seconds=max(window_seconds * 100, 3600))

    # Translate match_fields to post-unwind paths. After $unwind on
    # `items`, "items.product_ref" stays the same but is now a scalar
    # per emitted doc.
    group_id = {
        f.replace("items.", "item_"): f"${f}"
        for f in match_fields
    }
    return [
        {"$match": {"timestamp": {"$gte": cutoff}}},
        {"$unwind": {"path": "$items", "preserveNullAndEmptyArrays": False}},
        {"$match": {"items.charge_code": {"$in": redundant_codes}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": group_id,
            "txn_count":      {"$sum": 1},
            "txn_ids":        {"$push": "$transaction_id"},
            "charge_codes":   {"$addToSet": "$items.charge_code"},
            "amounts":        {"$push": "$items.line_total_myr"},
            "first_at":       {"$min": "$timestamp"},
            "last_at":        {"$max": "$timestamp"},
        }},
        {"$match": {
            "txn_count": {"$gte": 2},
            "$expr": {
                "$and": [
                    {"$gte": [{"$size": "$charge_codes"}, 2]},
                    {"$lte": [
                        {"$dateDiff": {"startDate": "$first_at", "endDate": "$last_at",
                                       "unit": "second"}},
                        window_seconds,
                    ]},
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "customer_id": "$_id.customer_id",
            "product_ref": "$_id.item_product_ref",
            "txn_count": 1, "txn_ids": 1, "charge_codes": 1, "amounts": 1,
            "first_at": 1, "last_at": 1,
            "rule_type": {"$literal": "double_charge_multi_code"},
            "rule_name": {"$literal": "Double charge across redundant charge codes"},
        }},
    ]


# --- proration_check (Phase B.2, V3 rewrite) -----------------------
def build_proration_check(params: dict) -> list[dict]:
    """Mid-cycle change whose proration amount diverges from the expected.

    V3 reads `items[0].metadata.is_mid_cycle_change` /
    `proration_amount_myr` / `expected_proration_myr` on the first
    subscription line.
    """
    types = params.get("applies_to_types", ["subscription_charge"])
    tolerance_pct = params.get("tolerance_pct", 0.05)
    return [
        {"$match": {"transaction_type": {"$in": types}}},
        {"$addFields": {
            "_first_item":      {"$arrayElemAt": ["$items", 0]},
        }},
        {"$addFields": {
            "_first_item_meta": {"$ifNull": ["$_first_item.metadata", {}]},
        }},
        {"$match": {"_first_item_meta.is_mid_cycle_change": True}},
        {"$addFields": {
            "_actual":   {"$ifNull": ["$_first_item_meta.proration_amount_myr", None]},
            "_expected": {"$ifNull": ["$_first_item_meta.expected_proration_myr", None]},
        }},
        {"$match": {
            "$expr": {
                "$or": [
                    {"$eq": ["$_actual", None]},
                    {"$eq": ["$_expected", None]},
                    {"$and": [
                        {"$gt": ["$_expected", 0]},
                        {"$gt": [
                            {"$divide": [
                                {"$abs": {"$subtract": ["$_actual", "$_expected"]}},
                                "$_expected",
                            ]},
                            tolerance_pct,
                        ]},
                    ]},
                ]
            }
        }},
        {"$project": {
            "_id": 0,
            "transaction_id": 1, "customer_id": 1,
            "total_myr": 1, "timestamp": 1,
            "transaction_type": 1, "cycle_id": 1,
            "actual_proration_myr":   "$_actual",
            "expected_proration_myr": "$_expected",
            "rule_type": {"$literal": "proration_check"},
            "rule_name": {"$literal": "Proration mismatch on mid-cycle change"},
        }},
    ]


# --- registry --------------------------------------------------------
RULE_BUILDERS: dict[str, RuleBuilder] = {
    "discount_mismatch":            build_discount_mismatch,
    "velocity_anomaly":             build_velocity_anomaly,
    "amount_outlier":               build_amount_outlier,
    "entitlement_mismatch":         build_entitlement_mismatch,
    "geographic_anomaly":           build_geographic_anomaly,
    "duplicate_transaction":        build_duplicate_transaction,
    # Phase B.2 — Acme-named rules
    "termination_fee_check":        build_termination_fee_check,
    "unearned_earned_segregation":  build_unearned_earned_segregation,
    "double_charge_multi_code":     build_double_charge_multi_code,
    "proration_check":              build_proration_check,
}


def build_rule_pipeline(rule_type: str, parameters: dict) -> list[dict]:
    """Dispatch to the right builder.

    Raises KeyError if rule_type is unknown — callers should validate via
    Pydantic discriminated union before reaching here.
    """
    builder = RULE_BUILDERS[rule_type]
    return builder(parameters)
