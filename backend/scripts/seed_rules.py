"""Seed default quarantine rules — one per rule_type, all in shadow mode.

Analysts can flip individual rules to active via the Rule Studio. ASP
processors mirror these for the live streams; the same parameters drive
both batch (test against history) and streaming evaluation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.constants import QUARANTINE_RULES
from app.core.logging import get_logger

logger = get_logger(__name__)


DEFAULT_RULES: list[dict] = [
    {
        "name": "Discount without active promotion",
        "rule_type": "discount_mismatch",
        "parameters": {"min_discount_amount_myr": 5.0},
        "severity": "medium",
    },
    {
        "name": "Velocity burst (>5 txns / 5 min)",
        "rule_type": "velocity_anomaly",
        "parameters": {
            "window_seconds": 300,
            "max_transactions": 5,
            "group_by": ["customer_id", "merchant_id"],
        },
        "severity": "high",
    },
    {
        "name": "Amount outlier (>3σ vs customer)",
        "rule_type": "amount_outlier",
        "parameters": {
            "std_dev_multiplier": 3.0,
            "lookback_days": 90,
            "minimum_history_count": 10,
        },
        "severity": "medium",
    },
    {
        "name": "PPV without entitlement",
        "rule_type": "entitlement_mismatch",
        "parameters": {},
        "severity": "high",
    },
    {
        "name": "Geographic anomaly vs home state",
        "rule_type": "geographic_anomaly",
        "parameters": {},
        "severity": "low",
    },
    {
        "name": "Duplicate transactions within 60s",
        "rule_type": "duplicate_transaction",
        "parameters": {
            "window_seconds": 60,
            "fields_to_match": ["customer_id", "merchant_id", "amount"],
        },
        "severity": "high",
    },
    # Phase B.2 — Acme-named rules
    {
        "name": "Termination fee against active or in-grace customer",
        "rule_type": "termination_fee_check",
        "parameters": {
            "charge_code": "CC_TERMINATION_FEE",
            "min_amount_myr": 50.0,
            "grace_period_days": 30,
        },
        "severity": "high",
    },
    {
        "name": "Earned/unearned revenue split missing",
        "rule_type": "unearned_earned_segregation",
        "parameters": {
            "applies_to_types": ["subscription_charge", "addon_purchase"],
            "tolerance_myr": 0.5,
        },
        "severity": "medium",
    },
    {
        "name": "Double charge across redundant codes (PPV direct + bundle)",
        "rule_type": "double_charge_multi_code",
        "parameters": {
            "redundant_codes": ["CC_PPV_DIRECT", "CC_BUNDLE_PPV"],
            "match_fields": ["customer_id", "content_id"],
            "window_seconds": 600,
        },
        "severity": "high",
    },
    {
        "name": "Proration mismatch on mid-cycle change",
        "rule_type": "proration_check",
        "parameters": {
            "applies_to_types": ["subscription_charge"],
            "tolerance_pct": 0.05,
        },
        "severity": "medium",
    },
]


async def seed_rules(db) -> int:
    coll = db[QUARANTINE_RULES]
    await coll.delete_many({})
    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    for r in DEFAULT_RULES:
        docs.append({
            "rule_id": f"rule_{uuid.uuid4().hex[:12]}",
            "name": r["name"],
            "rule_type": r["rule_type"],
            "parameters": r["parameters"],
            "severity": r["severity"],
            "enabled": True,
            "mode": "shadow",
            "hit_count": 0,
            "created_at": now,
            "updated_at": now,
            "created_by": "seed",
        })
    await coll.insert_many(docs, ordered=False)
    logger.info("rules_seeded", count=len(docs))
    return len(docs)
