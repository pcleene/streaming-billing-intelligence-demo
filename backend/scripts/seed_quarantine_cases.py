"""Seed open quarantine cases in the V3 rich shape.

Generates `count` open / in-review cases into `quarantine_cases` whose
documents satisfy `QuarantineCaseV3` end-to-end:

  * Identity & routing: customer_id + customer_type + account_id +
    parent_account_id + outlet_id + transaction_id + cycle_id + entity.
  * Lifecycle timeline (`lifecycle`) with backdated open / in-review
    transitions via `case_lifecycle` helpers.
  * `customer_snapshot` (CustomerSnapshotV3 — name/tier/ic_number/
    loyalty_member_id/package_at_billing/active_promotions_at_billing/
    active_entitlements_at_billing/lifetime_quarantine_count/tenure_months/
    churn_risk/service_state).
  * `transaction_summary` (TransactionSummary — timestamp/transaction_type/
    channel/subtotal_myr/total_discount_myr/tax_myr/total_myr/items_count/
    items_summary[ItemSummary]/payment_method/card_last4).
  * `similar_cases_preview` (top 3 SimilarCasePreview entries) so the
    side-panel renders without a vector lookup.
  * `ai_assist` projection (PR-AG agent_trace) for ~60% of cases.
  * `sla` with ~25% pre-breached rows so the SLA sweeper has something
    to surface.
Reads BOTH `customers_residential` and `customers_commercial` so the seeded
case mix mirrors the customer fleet. Falls back to synthetic ids if either
collection is empty.

Idempotency:
  All seeded `case_id`s are prefixed `seed_pr14_`. `main()` deletes any
  existing rows matching that prefix before inserting fresh ones.
  Production-created cases are never touched.

Determinism:
  Uses an instance-local `random.Random(42)`.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    QUARANTINE_CASES,
    SCHEMA_VERSION_V3,
)
from app.core.logging import configure_logging, get_logger
from app.services.case_lifecycle import (
    compute_priority,
    initial_sla,
    open_lifecycle_event,
    revenue_impact_from_summary,
    transition_lifecycle_event,
)

logger = get_logger(__name__)

SEED_PREFIX = "seed_pr14_"

RULE_TYPES = [
    "discount_mismatch",
    "velocity_anomaly",
    "amount_outlier",
    "geographic_anomaly",
    "duplicate_transaction",
    "entitlement_mismatch",
    "termination_fee_check",
]

SEVERITY_MIX: list[tuple[str, float]] = [
    ("medium", 0.40),
    ("high", 0.30),
    ("low", 0.20),
    ("critical", 0.10),
]

STATUS_MIX: list[tuple[str, float]] = [
    ("open", 0.70),
    ("under_review", 0.20),
    ("open", 0.10),  # treat needs_more_info as open + tag
]

LIKELIHOODS = ["true_positive", "false_positive", "needs_more_info"]

CHARGE_CODE_POOL = [
    "CC_SUB_MTHLY",
    "CC_PPV_STD",
    "CC_ADDON_STD",
    "CC_DEVICE_RENT",
    "CC_LATE",
    "CC_PROMO_REBATE",
    "CC_ETF",
]

TXN_TYPES = (
    "subscription_charge", "ppv_charge", "addon_charge",
    "device_fee", "late_fee", "termination_fee",
)
CHANNELS = (
    "self_serve_app", "ivr", "auto_debit", "billing_run",
    "field_agent", "retail_pos",
)
PAYMENT_METHODS = (
    "credit_card", "auto_debit", "fpx", "ewallet", "post_paid_invoice",
)

FAST_NODES = ["load_case", "classify", "vector_search", "synthesize", "persist"]
DEEP_NODES = [
    "load_case", "classify", "analytics", "vector_search",
    "synthesize", "persist",
]

RATIONALE_LIBRARY = [
    "Rule fired with high evidence strength.",
    "Customer history shows similar resolved disposition.",
    "Transaction amount is within typical band for this segment.",
    "Vector search returned strong precedent matches.",
    "No duplicate webhook detected in the last 24h.",
    "Promotion was active in CRM at time of charge.",
    "Geo anomaly matches a known travel pattern.",
    "Velocity burst is consistent with bulk PPV purchase.",
]
RECOMMENDED_STEPS_LIBRARY = [
    "Verify promotion sync timestamp against CRM.",
    "Confirm customer travel via support_events.",
    "Cross-check duplicate webhook idempotency key.",
    "Page on-call if amount_at_risk > 1000 MYR.",
    "Reach out via WhatsApp template T_FRAUD_VERIFY.",
    "Mark as false_positive if entitlement matches.",
    "Escalate to L2 with rationale bullets attached.",
    "Refund and block card if customer denies charge.",
]
NON_FATAL_ERRORS = [
    "vector_search: timeout retried OK",
    "analytics: drift snapshot stale by 3min",
    "classify: fallback heuristic engaged",
]
SIMILAR_CASE_SUMMARIES = [
    "Resolved as duplicate webhook — idempotency key collided.",
    "True positive: discount applied without active promo (CRM sync lag).",
    "False positive: customer was travelling; geo anomaly confirmed legitimate.",
    "True positive recharge: bulk PPV purchase via shared decoder.",
    "Needs more info: termination fee delta matched contract terms.",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    values = [v for v, _ in options]
    weights = [w for _, w in options]
    return rng.choices(values, weights=weights, k=1)[0]


# --- Rule + transaction summary ----------------------------------------

def _build_rules_triggered(
    rng: random.Random, *, severity: str, rule_types: list[str]
) -> list[dict]:
    return [
        {
            "rule_id":   f"rule_{rt}_001",
            "rule_type": rt,
            "rule_name": rt.replace("_", " ").title(),
            "severity":  severity,
            "evidence": {
                "score":             round(rng.uniform(0.55, 0.99), 3),
                "sample_txn_count":  rng.randint(1, 5),
            },
        }
        for rt in rule_types
    ]


def _build_item_summary(rng: random.Random, txn_type: str) -> dict:
    unit_price = round(rng.uniform(15.0, 500.0), 2)
    discount = round(unit_price * rng.uniform(0.0, 0.3), 2)
    return {
        "name":                  txn_type.replace("_", " ").title(),
        "category":              "subscription" if txn_type == "subscription_charge" else txn_type,
        "unit_price_myr":        unit_price,
        "discount_per_unit_myr": discount,
        "line_total_myr":        round(unit_price - discount, 2),
        "promo_id":              None,
        "charge_code":           rng.choice(CHARGE_CODE_POOL),
    }


def _build_transaction_summary(rng: random.Random) -> dict[str, Any]:
    """Full TransactionSummary V3 shape."""
    txn_type = rng.choice(TXN_TYPES)
    channel = rng.choice(CHANNELS)
    items_count = rng.randint(1, 3)
    items_summary = [_build_item_summary(rng, txn_type) for _ in range(items_count)]
    subtotal = round(sum(i["unit_price_myr"] for i in items_summary), 2)
    total_discount = round(sum(i["discount_per_unit_myr"] for i in items_summary), 2)
    tax = round((subtotal - total_discount) * 0.06, 2)
    total = round(subtotal - total_discount + tax, 2)
    payment_method = rng.choice(PAYMENT_METHODS)
    card_last4 = (
        f"{rng.randint(0, 9999):04d}"
        if payment_method == "credit_card" else None
    )
    return {
        "transaction_id":     f"txn_{uuid.uuid4().hex[:16]}",
        "timestamp":           _utcnow() - timedelta(minutes=rng.randint(5, 60 * 24)),
        "transaction_type":    txn_type,
        "channel":             channel,
        "subtotal_myr":        subtotal,
        "total_discount_myr":  total_discount,
        "tax_myr":             tax,
        "total_myr":           total,
        "items_count":         items_count,
        "items_summary":       items_summary,
        "payment_method":      payment_method,
        "card_last4":          card_last4,
    }


# --- Customer snapshot --------------------------------------------------

def _build_customer_snapshot(rng: random.Random, customer: dict) -> dict[str, Any]:
    """CustomerSnapshotV3 frozen at quarantine time (V3 flat-root reads)."""
    name = customer.get("name") or customer["customer_id"]
    state = (customer.get("address") or {}).get("state")
    subs = customer.get("subscriptions") or []
    pkg_label = None
    if subs:
        s0 = subs[0]
        pkg_label = s0.get("package_name") or s0.get("package_code")
    promos = []
    for p in customer.get("active_promotions") or []:
        promos.append({
            "promotion_code":      p.get("promotion_code"),
            "description":         p.get("description"),
            "discount_pct":        p.get("discount_pct"),
            "discount_amount_myr": p.get("discount_amount_myr"),
        })
    entitlements = [
        e.get("content_id")
        for e in (customer.get("entitlements") or [])
        if e.get("content_id")
    ]
    cem = customer.get("cross_entity_metrics") or {}
    return {
        "customer_id":                    customer["customer_id"],
        "name":                           name,
        "tier":                           customer.get("tier"),
        "ic_number":                      customer.get("ic_number"),
        "loyalty_member_id":              None,
        "package_at_billing":             pkg_label,
        "active_promotions_at_billing":   promos,
        "active_entitlements_at_billing": entitlements,
        "lifetime_quarantine_count":      customer.get("lifetime_quarantine_count", 0),
        "tenure_months":                  rng.randint(3, 96),
        "churn_risk":                     float(cem.get("churn_risk", 0.0)),
        "service_state":                  state,
    }


# --- AI assist (agent_trace) -------------------------------------------

def _build_agent_trace(
    rng: random.Random,
    *,
    started_at: datetime,
    nodes: list[str],
    duration_range: tuple[int, int],
    error_chance: float,
) -> list[dict]:
    cursor = started_at
    out: list[dict] = []
    for node in nodes:
        dur = rng.randint(*duration_range)
        finished = cursor + timedelta(milliseconds=dur)
        entry: dict[str, Any] = {
            "node":         node,
            "started_at":   cursor,
            "finished_at":  finished,
            "duration_ms":  float(dur),
            "output_keys":  [node + "_out"],
            "error":        None,
            "metadata":     {},
        }
        if rng.random() < error_chance:
            entry["error"] = rng.choice(NON_FATAL_ERRORS)
        out.append(entry)
        cursor = finished
    return out


def _build_ai_assist(
    rng: random.Random, *, case_opened_at: datetime,
) -> dict[str, Any]:
    is_fast = rng.random() < 0.5
    if is_fast:
        n = rng.randint(3, 4)
        nodes = FAST_NODES[:n]
        agent_trace = _build_agent_trace(
            rng, started_at=case_opened_at, nodes=nodes,
            duration_range=(50, 300), error_chance=0.0,
        )
        path = "fast"
    else:
        agent_trace = _build_agent_trace(
            rng, started_at=case_opened_at, nodes=list(DEEP_NODES),
            duration_range=(80, 700), error_chance=0.10,
        )
        path = "deep"

    rationale = rng.sample(RATIONALE_LIBRARY, k=rng.randint(2, 4))
    steps = rng.sample(RECOMMENDED_STEPS_LIBRARY, k=rng.randint(2, 4))
    likelihood = rng.choice(LIKELIHOODS)
    confidence = round(rng.uniform(0.45, 0.97), 3)
    degraded = rng.random() < 0.15
    return {
        "summary": (
            f"AI-assist ({path}) flagged this case as {likelihood} with "
            f"confidence {confidence}."
        ),
        "likelihood":        likelihood,
        "confidence":        confidence,
        "rationale":         rationale,
        "recommended_steps": steps,
        "references":        [],
        "retrieval": {
            "index_name":         "case_history_autoembed_idx",
            "k":                  5,
            "score_threshold":    0.7,
            "retrieved_case_ids": [
                f"hist_{uuid.uuid4().hex[:10]}"
                for _ in range(rng.randint(0, 3))
            ],
            "embedding_model":    "atlas-autoembed",
        },
        "generated_at":   case_opened_at + timedelta(seconds=rng.randint(2, 30)),
        "model":          "claude-opus-4",
        "degraded":       degraded,
        "degraded_reason": "fallback_synthesis" if degraded else None,
        "agent_trace":    agent_trace,
        "synthesis":      {"path": path, "degraded": degraded},
    }


# --- Similar-cases preview ---------------------------------------------

def _build_similar_cases_preview(rng: random.Random) -> list[dict]:
    n = rng.randint(2, 3)
    out: list[dict] = []
    for _ in range(n):
        out.append({
            "case_id":     f"hist_{uuid.uuid4().hex[:14]}",
            "relevance":   round(rng.uniform(0.65, 0.98), 3),
            "disposition": rng.choice([
                "true_positive_refund", "true_positive_recharge",
                "false_positive", "needs_human", "duplicate",
            ]),
            "resolved_at": _utcnow() - timedelta(days=rng.randint(7, 365)),
            "summary":     rng.choice(SIMILAR_CASE_SUMMARIES),
        })
    return out


# --- Case builder -------------------------------------------------------

def _build_case(
    rng: random.Random,
    customer: dict,
) -> dict[str, Any]:
    severity = _weighted_choice(rng, SEVERITY_MIX)
    status = _weighted_choice(rng, STATUS_MIX)
    n_rules = rng.randint(1, 2)
    rule_types_fired = rng.sample(RULE_TYPES, k=n_rules)

    customer_id = customer["customer_id"]
    customer_type = customer.get("customer_type", "residential")
    is_commercial = customer_type == "commercial"

    is_breached = rng.random() < 0.25
    if is_breached:
        opened_at = _utcnow() - timedelta(minutes=rng.randint(4_400, 4_500))
    else:
        opened_at = _utcnow() - timedelta(minutes=rng.randint(5, 6 * 60))

    rules_triggered = _build_rules_triggered(
        rng, severity=severity, rule_types=rule_types_fired,
    )
    transaction_summary = _build_transaction_summary(rng)
    revenue_impact = revenue_impact_from_summary(
        transaction_summary={
            "txn_id":       transaction_summary["transaction_id"],
            "total_myr":    transaction_summary["total_myr"],
            "charge_codes": [
                i["charge_code"] for i in transaction_summary["items_summary"]
            ],
        },
        severity=severity,
    )
    customer_snapshot = _build_customer_snapshot(rng, customer)
    score, band, drivers = compute_priority(
        severity=severity,
        amount_at_risk_myr=revenue_impact["amount_at_risk_myr"],
        customer_lifetime_quarantine_count=customer_snapshot[
            "lifetime_quarantine_count"
        ],
        is_commercial=is_commercial,
    )
    sla = initial_sla(severity=severity, opened_at=opened_at)
    if is_breached:
        breach_offset = rng.randint(1, 30)
        sla["is_breached"]      = True
        sla["breached_at"]      = _utcnow() - timedelta(minutes=breach_offset)
        sla["minutes_to_breach"] = float(-breach_offset)

    lifecycle: list[dict] = [
        open_lifecycle_event(
            actor_id="seed_pr14",
            rule_types_fired=rule_types_fired,
            note="seed:pr14",
        )
    ]
    lifecycle[0]["at"] = opened_at
    if status == "under_review":
        lifecycle.append(
            transition_lifecycle_event(
                from_status="open",
                to_status="under_review",
                actor_id="seed_pr14",
                actor_type="analyst",
                note="auto-routed for review",
            )
        )
        lifecycle[-1]["at"] = opened_at + timedelta(
            minutes=rng.randint(2, 45),
        )

    cycle_id = (
        customer.get("current_cycle", {}).get("cycle_id")
        if isinstance(customer.get("current_cycle"), dict) else None
    ) or f"CYC_{opened_at.strftime('%Y%m')}_{customer_id}"

    entity = (
        "acme_biz" if is_commercial else
        ((customer.get("entities") or ["acme_paytv"])[0])
    )

    case_id = f"{SEED_PREFIX}{uuid.uuid4().hex[:14]}"
    case: dict[str, Any] = {
        "_schema_version":        SCHEMA_VERSION_V3,
        "case_id":                case_id,

        # Identity & routing.
        "customer_id":            customer_id,
        "customer_type":          customer_type,
        "account_id":             customer.get("account_id"),
        "parent_account_id":      customer.get("parent_account_id"),
        "outlet_id":              customer.get("outlet_id"),
        "transaction_id":         transaction_summary["transaction_id"],
        "cycle_id":               cycle_id,
        "entity":                 entity,

        # Severity & priority.
        "severity":               severity,
        "status":                 status,
        "priority_score":         score,
        "priority_band":          band,
        "auto_priority_drivers":  drivers,
        "priority_drivers":       drivers,  # back-compat alias

        # Lifecycle.
        "lifecycle":              lifecycle,
        "lifecycle_events":       lifecycle,  # back-compat alias

        # Rule hits + frozen contexts.
        "rule_types_fired":       rule_types_fired,
        "rules_triggered":        rules_triggered,
        "customer_snapshot":      customer_snapshot,
        "transaction_summary":    transaction_summary,

        # AI assist (filled below for ~60% of cases).
        "ai_assist":              None,

        # Similar cases preview.
        "similar_cases_preview":  _build_similar_cases_preview(rng),

        # SLA + revenue impact.
        "sla":                    sla,
        "revenue_impact":         revenue_impact,

        # Tags.
        "tags":                   ["seed", "pr14", customer_type],

        # Timestamps.
        "opened_at":              opened_at,
        "created_at":             opened_at,
        "updated_at":             _utcnow(),
    }

    if rng.random() < 0.60:
        case["ai_assist"] = _build_ai_assist(rng, case_opened_at=opened_at)
    return case


# --- Customer pool ------------------------------------------------------

_CUSTOMER_PROJECTION = {
    "_id": 0,
    "customer_id":            1,
    "customer_type":          1,
    "name":                   1,
    "account_id":             1,
    "parent_account_id":      1,
    "outlet_id":              1,
    "tier":                   1,
    "ic_number":              1,
    "address":                1,
    "subscriptions":          1,
    "active_promotions":      1,
    "entitlements":           1,
    "entities":               1,
    "lifetime_quarantine_count": 1,
    "current_cycle":          1,
    "cross_entity_metrics":   1,
}


async def _load_customer_pool(db, *, limit: int = 200) -> list[dict]:
    """Read up to `limit` customers across both type-specific collections."""
    out: list[dict] = []
    half = max(1, limit // 2)
    for coll_name in (CUSTOMERS_RESIDENTIAL, CUSTOMERS_COMMERCIAL):
        cursor = db[coll_name].find({}, _CUSTOMER_PROJECTION).limit(half)
        async for doc in cursor:
            out.append(doc)
    return out


# --- Public entrypoint --------------------------------------------------

async def main(db, *, count: int = 25) -> dict[str, int]:
    """Seed `count` open-ish quarantine cases (V3 shape).

    Idempotent: deletes any existing `seed_pr14_*` rows first.

    Returns counters: ``{"deleted", "inserted", "with_ai_assist",
    "breached", "by_severity_*", "by_type_residential", "by_type_commercial"}``.
    """
    rng = random.Random(42)
    coll = db[QUARANTINE_CASES]

    deleted = await coll.delete_many({"case_id": {"$regex": f"^{SEED_PREFIX}"}})
    deleted_count = (
        deleted.deleted_count
        if hasattr(deleted, "deleted_count") else int(deleted or 0)
    )

    customer_pool = await _load_customer_pool(db, limit=200)
    if not customer_pool:
        # Synthetic fallback so the seeder still produces docs in a
        # fresh dev environment.
        customer_pool = [{
            "customer_id":  f"cust_{i:06d}",
            "customer_type": "residential",
            "account_id":   f"ACC_{i:06d}",
        } for i in range(count)]

    docs: list[dict] = []
    for _ in range(count):
        c = rng.choice(customer_pool)
        docs.append(_build_case(rng, c))

    if docs:
        await coll.insert_many(docs, ordered=False)

    counters: dict[str, int] = {
        "deleted":         deleted_count,
        "inserted":        len(docs),
        "with_ai_assist":  sum(1 for d in docs if d.get("ai_assist") is not None),
        "breached":        sum(
            1 for d in docs if (d.get("sla") or {}).get("is_breached") is True
        ),
        "by_type_residential": sum(
            1 for d in docs if d.get("customer_type") == "residential"
        ),
        "by_type_commercial": sum(
            1 for d in docs if d.get("customer_type") == "commercial"
        ),
    }
    for sev, _ in SEVERITY_MIX:
        counters[f"by_severity_{sev}"] = sum(
            1 for d in docs if d.get("severity") == sev
        )
    logger.info("seed_quarantine_cases_done", **counters)
    return counters


async def _entrypoint() -> None:
    from app.deps import connect_mongo, disconnect_mongo, get_db

    configure_logging()
    await connect_mongo()
    try:
        await main(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_entrypoint())
