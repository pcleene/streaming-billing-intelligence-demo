"""Apply $jsonSchema validators to the operational collections.

PR-1 rewrite — every collection in `ALL_COLLECTIONS` gets a validator.

Validation level is `moderate` + action `warn` so legacy seed data is not
rejected if a field drifts during demo iteration. Tighten to
`strict`/`error` for prod (PR-15 promotes selectively).

Validators describe the *new* PR-1 shape. Because validation is moderate,
legacy documents that lack the new fields continue to pass; only newly-
written documents that violate required-field constraints get warned.
"""

from __future__ import annotations

import asyncio

from app.core.constants import (
    BILL_CYCLES,
    CHARGE_CODES,
    CRM_SNAPSHOTS,
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    FEATURE_DRIFT_METRICS,
    FEATURES,
    QUARANTINE_CASES,
    QUARANTINE_CASES_HISTORY,
    QUARANTINE_RULES,
    SYSTEM_METRICS,
    TRANSACTIONS,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# =====================================================================
# Rule + case + transaction (legacy + extended fields)
# =====================================================================

RULE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["rule_id", "name", "rule_type", "parameters", "mode", "enabled"],
        "properties": {
            "rule_id":   {"bsonType": "string"},
            "name":      {"bsonType": "string", "minLength": 3, "maxLength": 140},
            "rule_type": {
                "enum": [
                    "discount_mismatch", "velocity_anomaly", "amount_outlier",
                    "entitlement_mismatch", "geographic_anomaly", "duplicate_transaction",
                    # Phase B.2 — Acme-named rules
                    "termination_fee_check", "unearned_earned_segregation",
                    "double_charge_multi_code", "proration_check",
                ]
            },
            "parameters": {"bsonType": "object"},
            "mode":       {"enum": ["shadow", "active", "disabled"]},
            "enabled":    {"bsonType": "bool"},
            "severity":   {"enum": ["low", "medium", "high"]},
            "hit_count":  {"bsonType": ["int", "long"], "minimum": 0},
            "_schema_version": {"bsonType": ["int", "long"]},
        },
    }
}


CASE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["case_id", "customer_id", "rules_triggered", "status", "severity"],
        "properties": {
            "case_id":     {"bsonType": "string"},
            "customer_id": {"bsonType": "string"},
            "cycle_id":    {"bsonType": ["string", "null"]},
            "status":      {"enum": ["open", "under_review", "resolved", "dismissed"]},
            "severity":    {"enum": ["low", "medium", "high", "critical"]},
            "priority_band": {"enum": ["P0", "P1", "P2", "P3", None]},
            "disposition": {
                "enum": [None, "true_positive", "false_positive", "escalate", "needs_more_info"]
            },
            "rules_triggered": {"bsonType": "array", "minItems": 1},
            "_schema_version": {"bsonType": ["int", "long"]},
            # Phase B.4 — standardized ai_assist shape. Optional so legacy
            # cases pre-B.4 still pass under moderate validation.
            "ai_assist": {
                "bsonType": ["object", "null"],
                "required": ["summary", "likelihood", "confidence",
                             "rationale", "recommended_steps"],
                "properties": {
                    "summary":    {"bsonType": "string"},
                    "likelihood": {
                        "enum": ["true_positive", "false_positive", "needs_more_info"]
                    },
                    "confidence": {
                        "bsonType": ["double", "int", "long"],
                        "minimum": 0.0, "maximum": 1.0,
                    },
                    "rationale":         {"bsonType": "array", "minItems": 1},
                    "recommended_steps": {"bsonType": "array", "minItems": 1},
                    "references":        {"bsonType": "array"},
                    "retrieval": {
                        "bsonType": ["object", "null"],
                        "properties": {
                            "index_name": {"bsonType": "string"},
                            "k": {"bsonType": ["int", "long"], "minimum": 1},
                            "score_threshold": {
                                "bsonType": ["double", "int", "long"],
                                "minimum": 0.0, "maximum": 1.0,
                            },
                            "retrieved_case_ids": {"bsonType": "array"},
                            "embedding_model": {"bsonType": ["string", "null"]},
                        },
                    },
                    "generated_at":    {"bsonType": ["date", "string"]},
                    "model":           {"bsonType": ["string", "null"]},
                    "degraded":        {"bsonType": "bool"},
                    "degraded_reason": {"bsonType": ["string", "null"]},
                },
            },
            # PR-7+ rich shape: SLA + revenue impact (optional)
            "sla": {
                "bsonType": ["object", "null"],
                "properties": {
                    "target_resolution_at": {"bsonType": ["date", "string"]},
                    "elapsed_minutes":      {"bsonType": ["double", "int", "long"]},
                    "minutes_to_breach":    {"bsonType": ["double", "int", "long"]},
                    "is_breached":          {"bsonType": "bool"},
                    "breached_at":          {"bsonType": ["date", "string", "null"]},
                },
            },
        },
    }
}


CASE_HISTORY_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "case_id", "customer_id", "rules_triggered",
            "disposition", "resolved_at", "embed_source",
        ],
        "properties": {
            "case_id":     {"bsonType": "string"},
            "customer_id": {"bsonType": "string"},
            "customer_tier": {"bsonType": ["string", "null"]},
            "rules_triggered": {"bsonType": "array", "minItems": 1},
            "disposition": {
                "enum": [
                    # Legacy lean enum
                    "true_positive", "false_positive", "escalate", "needs_more_info",
                    # PR-8 rich enum
                    "true_positive_refund", "true_positive_recharge",
                    "needs_human", "duplicate",
                ]
            },
            "severity":    {"enum": ["low", "medium", "high", "critical"]},
            "resolved_at": {"bsonType": ["date", "string"]},
            # AutoEmbed (ADR-032): the AutoEmbed index reads `embed_source.text`
            # and stores the vector invisibly. The leaf is required at depth 1
            # under a single object — Atlas silently skips arrays/objects at
            # the indexed path.
            "embed_source": {
                "bsonType": "object",
                "required": ["text"],
                "properties": {
                    "text": {"bsonType": "string", "minLength": 1},
                },
            },
            "_schema_version": {"bsonType": ["int", "long"]},
        },
    }
}


TRANSACTION_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_schema_version", "transaction_id", "customer_id",
            "customer_type", "account_id", "cycle_id",
            "timestamp", "transaction_type",
            "customer_summary", "items", "subtotal_myr", "total_myr",
        ],
        "properties": {
            "_schema_version":   {"bsonType": ["int", "long"]},
            "transaction_id":    {"bsonType": "string"},
            "customer_id":       {"bsonType": "string"},
            "customer_type":     {"enum": ["residential", "commercial"]},
            "account_id":        {"bsonType": "string"},
            "outlet_id":         {"bsonType": ["string", "null"]},
            "parent_account_id": {"bsonType": ["string", "null"]},
            "cycle_id":          {"bsonType": "string"},
            "bill_period":       {"bsonType": ["object", "null"]},
            "timestamp":         {"bsonType": ["date", "string"]},
            "transaction_type": {
                "enum": [
                    "subscription_charge", "addon_charge", "ppv_charge",
                    "termination_fee", "device_fee", "late_fee", "refund",
                    "billing_adjustment", "promo_rebate", "device_purchase",
                ]
            },
            "channel":           {"bsonType": ["string", "null"]},
            "entity":            {"bsonType": ["string", "null"]},
            "merchant_id":       {"bsonType": ["string", "null"]},
            "currency":          {"bsonType": "string"},
            "customer_summary":  {"bsonType": "object"},
            "items":             {"bsonType": "array"},
            "discounts":         {"bsonType": ["array", "null"]},
            "subtotal_myr":      {"bsonType": ["double", "int", "long"]},
            "total_discount_myr": {"bsonType": ["double", "int", "long"]},
            "tax":               {"bsonType": ["object", "null"]},
            "total_myr":         {"bsonType": ["double", "int", "long"]},
            "payment":           {"bsonType": ["object", "null"]},
            "loyalty":           {"bsonType": ["object", "null"]},
            "quarantined":       {"bsonType": "bool"},
            "quarantine_case_ids": {"bsonType": ["array", "null"]},
            "campaign_attribution": {"bsonType": ["object", "null"]},
            "computed_signals":  {"bsonType": ["object", "null"]},
        },
    }
}


# =====================================================================
# Customers (residential / commercial / index) + legacy
# =====================================================================

# Residential — V3 flat-root shape (PR-15). `unified_profile` was retired,
# its scalars lifted to root, and `contact` + `address` remain embedded but
# at root level. `tier` is canonical (no `segment`). No embedding fields
# (vector search runs only on `quarantine_cases_history`).
CUSTOMER_RESIDENTIAL_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_schema_version", "customer_id", "customer_type",
            "account_id", "name", "tier",
        ],
        "properties": {
            "_schema_version": {"bsonType": ["int", "long"]},
            "customer_id":     {"bsonType": "string"},
            "customer_type":   {"enum": ["residential"]},
            "account_id":      {"bsonType": "string"},
            "name":            {"bsonType": "string"},
            "tier":            {"bsonType": "string"},

            # Identity scalars (lifted from unified_profile)
            "preferred_name":  {"bsonType": ["string", "null"]},
            "ic_number":       {"bsonType": ["string", "null"]},
            "date_of_birth":   {"bsonType": ["date", "string", "null"]},
            "ethnicity":       {"bsonType": ["string", "null"]},
            "gender":          {"bsonType": ["string", "null"]},
            "marital_status":  {"bsonType": ["string", "null"]},
            "household_size":  {"bsonType": ["int", "long", "null"]},
            "occupation_band": {"bsonType": ["string", "null"]},

            # Embedded identity blocks (root-level)
            "contact": {
                "bsonType": ["object", "null"],
                "properties": {
                    "email":     {"bsonType": ["string", "null"]},
                    "phone":     {"bsonType": ["string", "null"]},
                    "alt_phone": {"bsonType": ["string", "null"]},
                },
            },
            "address": {
                "bsonType": ["object", "null"],
                "properties": {
                    "line1":   {"bsonType": "string"},
                    "city":    {"bsonType": "string"},
                    "state":   {"bsonType": "string"},
                    "country": {"bsonType": "string"},
                    "location": {
                        "bsonType": ["object", "null"],
                        "properties": {
                            "type":        {"enum": ["Point"]},
                            "coordinates": {"bsonType": "array", "minItems": 2, "maxItems": 2},
                        },
                    },
                },
            },

            # Hot-path embeds
            "recent_transactions": {"bsonType": ["array", "null"]},
            "open_cases": {
                "bsonType": ["array", "null"],
                "items": {
                    "bsonType": "object",
                    "required": ["case_id", "severity", "status", "created_at"],
                    "properties": {
                        "case_id":  {"bsonType": "string"},
                        "severity": {"enum": ["low", "medium", "high", "critical"]},
                        "status":   {"enum": ["open", "under_review"]},
                    },
                },
            },
            "latest_features": {"bsonType": ["object", "null"]},

            # Phase B.6 / PR-1 — recommendations subdoc (unchanged shape).
            "recommendations": {
                "bsonType": ["object", "null"],
                "properties": {
                    "computed_at": {"bsonType": ["date", "string"]},
                    "churn_risk": {
                        "bsonType": "object",
                        "required": ["band", "score"],
                        "properties": {
                            "band":  {"enum": ["low", "medium", "high"]},
                            "score": {
                                "bsonType": ["double", "int", "long"],
                                "minimum": 0.0, "maximum": 1.0,
                            },
                            "drivers": {"bsonType": "array"},
                        },
                    },
                    "next_best_offers": {
                        "bsonType": "array",
                        "items": {
                            "bsonType": "object",
                            "required": [
                                "offer_id", "offer_type", "title",
                                "rationale", "priority",
                            ],
                            "properties": {
                                "offer_id":   {"bsonType": "string"},
                                "offer_type": {
                                    "enum": [
                                        "upgrade", "addon",
                                        "retention_discount", "winback",
                                        "loyalty_perk",
                                    ]
                                },
                                "title":     {"bsonType": "string"},
                                "rationale": {"bsonType": "string"},
                                "expected_uplift_myr": {
                                    "bsonType": ["double", "int", "long"],
                                    "minimum": 0,
                                },
                                "priority": {
                                    "bsonType": ["int", "long"],
                                    "minimum": 1, "maximum": 10,
                                },
                            },
                        },
                    },
                },
            },
        },
    }
}


# Commercial — V3 flat-root shape (PR-15). `business_profile` is canonical
# (legacy `commercial_profile` alias retired). No top-level
# `business_registration_no` (lives in `business_profile`). No embeddings
# (vector search runs only on `quarantine_cases_history`).
CUSTOMER_COMMERCIAL_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_schema_version", "customer_id", "customer_type",
            "account_id", "name", "tier", "business_profile",
        ],
        "properties": {
            "_schema_version":   {"bsonType": ["int", "long"]},
            "customer_id":       {"bsonType": "string"},
            "customer_type":     {"enum": ["commercial"]},
            "account_id":        {"bsonType": "string"},
            "parent_account_id": {"bsonType": ["string", "null"]},
            "outlet_id":         {"bsonType": ["string", "null"]},
            "name":              {"bsonType": "string"},
            "tier":              {"bsonType": "string"},

            "business_profile": {
                "bsonType": "object",
                "properties": {
                    "business_name":            {"bsonType": "string"},
                    "business_registration_no": {"bsonType": ["string", "null"]},
                    "business_type":            {"bsonType": ["string", "null"]},
                    "industry":                 {"bsonType": ["string", "null"]},
                    "registered_address": {
                        "bsonType": ["object", "null"],
                        "properties": {
                            "location": {"bsonType": ["object", "null"]},
                        },
                    },
                },
            },

            "open_cases":      {"bsonType": ["array", "null"]},
            "latest_features": {"bsonType": ["object", "null"]},

            # Bucket pattern (≤500 transactions per cycle; one bucket per
            # cycle). Validated as a list of opaque sub-documents at this
            # level.
            "transaction_buckets": {"bsonType": ["array", "null"]},
        },
    }
}


CUSTOMER_INDEX_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["customer_id", "customer_type"],
        "properties": {
            "customer_id":       {"bsonType": "string"},
            "customer_type":     {"enum": ["residential", "commercial"]},
            "account_id":        {"bsonType": ["string", "null"]},
            "parent_account_id": {"bsonType": ["string", "null"]},
            "name":              {"bsonType": ["string", "null"]},
            "_schema_version":   {"bsonType": ["int", "long"]},
        },
    }
}


# =====================================================================
# Bill cycles + charge codes (PR-1 / PR-4 / PR-5)
# =====================================================================

BILL_CYCLE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["cycle_id", "customer_id", "cycle_start", "cycle_end", "status"],
        "properties": {
            "cycle_id":    {"bsonType": "string"},
            "customer_id": {"bsonType": "string"},
            "cycle_start": {"bsonType": ["date", "string"]},
            "cycle_end":   {"bsonType": ["date", "string"]},
            "status":      {"enum": ["open", "billed", "closed", "voided"]},
            "_schema_version": {"bsonType": ["int", "long"]},
        },
    }
}


CHARGE_CODE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["code", "description"],
        "properties": {
            "code":             {"bsonType": "string", "minLength": 2},
            "description":      {"bsonType": "string"},
            "revenue_category": {"bsonType": ["string", "null"]},
            "active":           {"bsonType": "bool"},
            "_schema_version":  {"bsonType": ["int", "long"]},
        },
    }
}


# =====================================================================
# Features + CRM snapshots + system metrics + drift
# =====================================================================

FEATURES_VALIDATOR = {
    # Canonical features-collection shape is defined by
    # `app.schemas.feature.canonical_feature_doc` — flat top-level fields
    # only. Validator below pins identity + sub-doc keys; the dozens of
    # numeric feature columns are not enumerated here because validators
    # run at validationLevel `moderate` and adding every column would
    # only buy us false rejections when the canonical list grows.
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["customer_id"],
        "properties": {
            "customer_id":         {"bsonType": "string"},
            "as_of":               {"bsonType": ["date", "string", "null"]},
            "feature_set_version": {"bsonType": ["string", "null"]},
            "_schema_version":     {"bsonType": ["int", "long"]},
            "lineage":             {"bsonType": ["object", "null"]},
            "quality":             {"bsonType": ["object", "null"]},
        },
    }
}


CRM_SNAPSHOT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["customer_id", "snapshot_at"],
        "properties": {
            "customer_id":     {"bsonType": "string"},
            "snapshot_at":     {"bsonType": ["date", "string"]},
            "_schema_version": {"bsonType": ["int", "long"]},
            "fields_present":  {"bsonType": ["array", "null"]},
            "fields_missing":  {"bsonType": ["array", "null"]},
            "fields_stale":    {"bsonType": ["array", "null"]},
            "diff_vs_current": {"bsonType": ["object", "null"]},
        },
    }
}


SYSTEM_METRICS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["recorded_at", "mode", "observed_tps"],
        "properties": {
            "recorded_at":  {"bsonType": "date"},
            "mode":         {"enum": ["steady", "burst", "idle"]},
            "burst_run_id": {"bsonType": ["string", "null"]},
            "observed_tps": {"bsonType": ["double", "int", "long"], "minimum": 0},
            "p50_ms_ingest": {"bsonType": ["double", "int", "long"], "minimum": 0},
            "p99_ms_ingest": {"bsonType": ["double", "int", "long"], "minimum": 0},
            "quarantine_per_sec": {"bsonType": ["double", "int", "long"], "minimum": 0},
            "rule_eval_p99_ms":   {"bsonType": ["double", "int", "long"], "minimum": 0},
            "txns_in_window":     {"bsonType": ["int", "long"], "minimum": 0},
            "cases_in_window":    {"bsonType": ["int", "long"], "minimum": 0},
        },
    }
}


FEATURE_DRIFT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["feature_name", "measured_at"],
        "properties": {
            "feature_name": {"bsonType": "string"},
            "measured_at":  {"bsonType": ["date", "string"]},
            "severity":     {"enum": ["none", "watch", "warn", "alert"]},
            "ks_statistic": {"bsonType": ["double", "int", "long"]},
            "p_value":      {"bsonType": ["double", "int", "long"]},
            "drift_detected": {"bsonType": ["bool", "null"]},
            "recommended_action": {
                "enum": ["monitor", "investigate", "retrain", "alert_oncall", None]
            },
        },
    }
}


# =====================================================================
# Registry
# =====================================================================

VALIDATORS: dict[str, dict] = {
    # Legacy + transitional alias. CUSTOMERS resolves to
    # CUSTOMERS_RESIDENTIAL after PR-1 — declare both keys explicitly so
    # the residential validator is applied even if the alias is removed.
    CUSTOMERS_RESIDENTIAL:    CUSTOMER_RESIDENTIAL_VALIDATOR,
    CUSTOMERS_COMMERCIAL:     CUSTOMER_COMMERCIAL_VALIDATOR,
    CUSTOMER_INDEX:           CUSTOMER_INDEX_VALIDATOR,

    QUARANTINE_RULES:         RULE_VALIDATOR,
    QUARANTINE_CASES:         CASE_VALIDATOR,
    QUARANTINE_CASES_HISTORY: CASE_HISTORY_VALIDATOR,
    TRANSACTIONS:             TRANSACTION_VALIDATOR,

    BILL_CYCLES:              BILL_CYCLE_VALIDATOR,
    CHARGE_CODES:             CHARGE_CODE_VALIDATOR,

    FEATURES:                 FEATURES_VALIDATOR,
    CRM_SNAPSHOTS:            CRM_SNAPSHOT_VALIDATOR,
    SYSTEM_METRICS:           SYSTEM_METRICS_VALIDATOR,
    FEATURE_DRIFT_METRICS:    FEATURE_DRIFT_VALIDATOR,
}

# Guard against the alias drifting away from CUSTOMERS_RESIDENTIAL.
if CUSTOMERS != CUSTOMERS_RESIDENTIAL:  # pragma: no cover - alias guard
    VALIDATORS[CUSTOMERS] = CUSTOMER_RESIDENTIAL_VALIDATOR


async def apply_validators(db) -> None:
    existing = await db.list_collection_names()
    for name, validator in VALIDATORS.items():
        if name not in existing:
            await db.create_collection(
                name,
                validator=validator,
                validationLevel="moderate",
                validationAction="warn",
            )
            logger.info("validator_created", collection=name)
        else:
            await db.command({
                "collMod": name,
                "validator": validator,
                "validationLevel": "moderate",
                "validationAction": "warn",
            })
            logger.info("validator_updated", collection=name)


async def _main() -> None:
    """Standalone entry: `python -m scripts.setup_validators`."""
    from app.core.logging import configure_logging
    from app.deps import connect_mongo, disconnect_mongo, get_db

    configure_logging()
    await connect_mongo()
    try:
        await apply_validators(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_main())
