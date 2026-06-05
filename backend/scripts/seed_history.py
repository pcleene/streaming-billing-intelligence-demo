"""Seed historical resolved quarantine cases — the RAG corpus (V4 shape).

Each case carries:

* identity (customer_id, customer_type, customer_tier, transaction_id,
  cycle_id, entity);
* frozen rule + transaction context (rules_triggered, transaction_summary
  in the V3 TransactionSummary shape, customer_context_summary);
* investigation outcome (disposition, severity, analyst_id/notes,
  resolution_summary, resolved_at, resolution_time_minutes,
  resolution_path, actions_taken, compensation_to_customer_myr);
* LLM-readable ``learnings`` (pattern_name, root_cause,
  what_would_have_prevented, false/true_positive_indicators,
  confidence_in_learning);
* the AutoEmbed payload at ``embed_source.text`` — Atlas embeds the
  string and stores the vector invisibly (ADR-032);
* provenance & feedback (used_in_rag_count, last_used_in_rag_at,
  rag_relevance_feedback, similar_resolved_cases).

Reads flat-root V3 documents from ``customers_residential``. The seed
no longer talks to Voyage directly — `setup_indexes.py` creates the
``case_history_autoembed_idx`` AutoEmbed index, Atlas does the rest.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from app.core.constants import (
    CUSTOMERS_RESIDENTIAL,
    QUARANTINE_CASES_HISTORY,
    SCHEMA_VERSION_V4,
)
from app.core.logging import get_logger
from app.services.embed_text_builder import build_history_embed_text

from scripts._realism import (
    ANALYST_NOTE_TEMPLATES,
    CHARGE_CODES,
    LEARNING_NARRATIVES,
    pick_package,
    pick_ppv_title,
    pick_promotion_for,
    render_analyst_note,
    render_resolution_summary,
)

logger = get_logger(__name__)

random.seed(44)
_RNG = random.Random(44)

RULE_TYPES = [
    "discount_mismatch",
    "velocity_anomaly",
    "amount_outlier",
    "entitlement_mismatch",
    "geographic_anomaly",
    "duplicate_transaction",
    "termination_fee_check",
]

# Disposition enum supports both legacy and rich values per the validator.
DISPOSITIONS = [
    "true_positive_refund",
    "true_positive_recharge",
    "false_positive",
    "needs_human",
    "duplicate",
]

# Map raw seed dispositions to the (disposition, pattern) keys used by
# ANALYST_NOTE_TEMPLATES so the rendered narrative matches the case
# outcome.
_DISPOSITION_TO_NARRATIVE_KEY: dict[str, str] = {
    "true_positive_refund":   "fraud",
    "true_positive_recharge": "fraud",
    "false_positive":         "legitimate",
    "needs_human":            "data_error",
    "duplicate":              "data_error",
}

SEVERITIES = ["low", "medium", "high", "critical"]

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

RESOLUTION_PATHS = (
    "auto_resolve", "analyst_review", "ai_assisted_review", "escalation",
)

ACTIONS_LIBRARY = [
    "refund_issued", "card_reissued", "block_card", "kyc_reverify",
    "promotion_synced", "whitelist_segment", "rebill_after_dupe_dedupe",
    "manual_credit_apply", "escalate_l2",
]

LEARNING_PATTERNS = list(LEARNING_NARRATIVES.keys())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------
# Transaction-summary builder (now uses realism pools for items)
# ---------------------------------------------------------------------

def _build_item_summary(txn_type: str) -> dict:
    """Pick a realistic item from the realism pools, biased to the txn type."""
    if txn_type == "subscription_charge":
        pkg = pick_package(_RNG)
        unit_price = pkg[2] if pkg[2] > 0 else round(_RNG.uniform(49.0, 200.0), 2)
        name = pkg[1]
        category = "subscription"
        charge_code = "CC_SUB_MTHLY"
    elif txn_type == "ppv_charge":
        ppv = pick_ppv_title(_RNG)
        unit_price = ppv[2] if ppv[2] > 0 else round(_RNG.uniform(9.0, 35.0), 2)
        name = ppv[1]
        category = "ppv"
        charge_code = "CC_PPV_STD"
    elif txn_type == "addon_charge":
        pkg = pick_package(_RNG, segment="value")
        unit_price = round(_RNG.uniform(9.0, 49.0), 2)
        name = pkg[1] + " (add-on)"
        category = "addon"
        charge_code = "CC_ADDON_STD"
    elif txn_type == "device_fee":
        unit_price = round(_RNG.uniform(15.0, 45.0), 2)
        name = "Acme Ultra Box monthly rental"
        category = "device"
        charge_code = "CC_DEVICE_RENT"
    elif txn_type == "late_fee":
        unit_price = round(_RNG.uniform(5.0, 25.0), 2)
        name = "Late payment fee"
        category = "late_fee"
        charge_code = "CC_LATE"
    elif txn_type == "termination_fee":
        unit_price = round(_RNG.uniform(199.0, 599.0), 2)
        name = "Early termination fee"
        category = "etf"
        charge_code = "CC_ETF"
    else:
        unit_price = round(_RNG.uniform(15.0, 600.0), 2)
        name = txn_type.replace("_", " ").title()
        category = txn_type
        charge_code = _RNG.choice(CHARGE_CODES)

    discount = round(unit_price * _RNG.uniform(0.0, 0.35), 2)
    return {
        "name":                  name,
        "category":              category,
        "unit_price_myr":        unit_price,
        "discount_per_unit_myr": discount,
        "line_total_myr":        round(unit_price - discount, 2),
        "promo_id":              None,
        "charge_code":           charge_code,
    }


def _build_transaction_summary(resolved_at: datetime) -> dict:
    txn_type = _RNG.choice(TXN_TYPES)
    items_count = _RNG.randint(1, 3)
    items_summary = [_build_item_summary(txn_type) for _ in range(items_count)]
    subtotal = round(sum(i["unit_price_myr"] for i in items_summary), 2)
    total_discount = round(sum(i["discount_per_unit_myr"] for i in items_summary), 2)
    tax = round((subtotal - total_discount) * 0.06, 2)
    total = round(subtotal - total_discount + tax, 2)
    pm = _RNG.choice(PAYMENT_METHODS)
    last4 = f"{_RNG.randint(0, 9999):04d}" if pm == "credit_card" else None
    return {
        "transaction_id":     f"txn_{uuid.uuid4().hex[:16]}",
        "timestamp":          resolved_at - timedelta(minutes=_RNG.randint(60, 60 * 24 * 30)),
        "transaction_type":   txn_type,
        "channel":            _RNG.choice(CHANNELS),
        "subtotal_myr":       subtotal,
        "total_discount_myr": total_discount,
        "tax_myr":            tax,
        "total_myr":          total,
        "items_count":        items_count,
        "items_summary":      items_summary,
        "payment_method":     pm,
        "card_last4":         last4,
    }


# ---------------------------------------------------------------------
# Learnings (pattern, root_cause, what_would_have_prevented)
# ---------------------------------------------------------------------

def _build_learnings(disposition: str, customer_ctx: dict) -> tuple[dict, dict]:
    """Pick a learning pattern and render the per-pattern narrative.

    Returns ``(learnings_doc, render_context)``. ``render_context`` is the
    same dict we passed to ``str.format`` so analyst-note rendering can
    reuse the same parametrisation.
    """
    pattern = _RNG.choice(LEARNING_PATTERNS)
    narrative = LEARNING_NARRATIVES[pattern]

    home_state = customer_ctx.get("home_state") or "Selangor"
    away_state = customer_ctx.get("away_state") or "Sabah"
    promo = pick_promotion_for(
        _RNG,
        tier=customer_ctx.get("tier"),
        tenure_months=customer_ctx.get("tenure_months", 0),
    )
    ctx = {
        "lag_hours":         _RNG.randint(11, 18),
        "promo_name":        promo[1],
        "promo_code":        promo[0],
        "valid_to":          promo[3][1],
        "burst_count":       _RNG.randint(5, 9),
        "burst_minutes":     _RNG.randint(60, 120),
        "swap_window_hours": _RNG.randint(24, 48),
        "outlier_ratio":     round(_RNG.uniform(1.4, 2.1), 1),
        "actual_etf":        round(_RNG.uniform(199.0, 299.0), 2),
        "expected_etf":      round(_RNG.uniform(549.0, 699.0), 2),
        "home_state":        home_state,
        "away_state":        away_state,
        "addon_name":        "Disney+ Hotstar",
        "addon_date":        (_utcnow() - timedelta(days=_RNG.randint(3, 25))).strftime("%Y-%m-%d"),
        "ticket_num":        f"{_RNG.randint(10000, 99999)}",
        "tier":              customer_ctx.get("tier") or "gold",
        "state":             home_state,
        "amount":            round(_RNG.uniform(15.0, 95.0), 2),
        "pattern_name":      narrative["pattern_name"],
    }

    def _safe_format(template: str) -> str:
        try:
            return template.format(**ctx)
        except (KeyError, IndexError):
            return template

    if disposition.startswith("true_positive"):
        tp_inds = _RNG.sample([
            "amount_outlier_>_8x_avg",
            "velocity_burst_5min",
            "card_used_in_3_geos_24h",
            "etf_billed_<_expected",
        ], k=_RNG.randint(2, 3))
        fp_inds: list[str] = []
    elif disposition == "false_positive":
        tp_inds = []
        fp_inds = _RNG.sample([
            "promo_present_in_crm_at_billing",
            "support_ticket_confirms_travel",
            "household_pattern_matches_history",
            "tier_change_explains_amount",
        ], k=_RNG.randint(2, 3))
    else:
        tp_inds = ["needs_more_info_evidence"]
        fp_inds = ["needs_more_info_evidence"]

    learnings = {
        "pattern_name":              narrative["pattern_name"],
        "root_cause":                _safe_format(narrative["root_cause"]),
        "what_would_have_prevented": _safe_format(narrative["what_would_have_prevented"]),
        "false_positive_indicators": fp_inds,
        "true_positive_indicators":  tp_inds,
        "confidence_in_learning":    round(_RNG.uniform(0.55, 0.95), 3),
    }
    return learnings, {"pattern_key": pattern, "ctx": ctx}


# ---------------------------------------------------------------------
# History doc
# ---------------------------------------------------------------------

def _build_history_doc(idx: int, customer_pool: list[dict]) -> dict:
    customer = _RNG.choice(customer_pool) if customer_pool else {}
    cid = customer.get("customer_id") or f"cust_unknown_{idx}"
    customer_type = customer.get("customer_type", "residential")
    tier = customer.get("tier") or _RNG.choice(["bronze", "silver", "gold", "platinum"])
    state = (customer.get("address") or {}).get("state") or "Selangor"
    away_state_pool = ("Sabah", "Sarawak", "Penang", "Johor", "Pahang", "Terengganu")
    away_state = _RNG.choice([s for s in away_state_pool if s != state])
    tenure_months = int(customer.get("tenure_months") or _RNG.randint(3, 96))

    customer_ctx = {
        "tier":           tier,
        "home_state":     state,
        "away_state":     away_state,
        "tenure_months":  tenure_months,
    }

    rules = _RNG.sample(RULE_TYPES, k=_RNG.randint(1, 2))
    rules_triggered = [
        {
            "rule_id":   f"rule_{rt}_001",
            "rule_type": rt,
            "rule_name": rt.replace("_", " ").title(),
            "severity":  _RNG.choice(SEVERITIES),
            "evidence": {
                "score":            round(_RNG.uniform(0.55, 0.99), 3),
                "sample_txn_count": _RNG.randint(1, 6),
                "fired_at":         _utcnow() - timedelta(days=_RNG.randint(7, 365)),
            },
        }
        for rt in rules
    ]

    disposition = _RNG.choices(
        DISPOSITIONS,
        weights=[0.30, 0.20, 0.30, 0.10, 0.10],
        k=1,
    )[0]
    severity = _RNG.choices(SEVERITIES, weights=[0.20, 0.45, 0.25, 0.10], k=1)[0]
    resolved_at = _utcnow() - timedelta(days=_RNG.randint(1, 365))
    transaction_summary = _build_transaction_summary(resolved_at)
    resolution_time_minutes = _RNG.randint(5, 60 * 24)
    resolution_path = _RNG.choice(RESOLUTION_PATHS)
    actions = _RNG.sample(ACTIONS_LIBRARY, k=_RNG.randint(1, 3))
    compensation = (
        round(_RNG.uniform(0.0, transaction_summary["total_myr"]), 2)
        if disposition == "true_positive_refund" else 0.0
    )

    learnings, render_meta = _build_learnings(disposition, customer_ctx)
    pattern_key = render_meta["pattern_key"]
    render_ctx = render_meta["ctx"]
    narrative_key = _DISPOSITION_TO_NARRATIVE_KEY.get(disposition, "legitimate")
    has_curated = (narrative_key, pattern_key) in ANALYST_NOTE_TEMPLATES
    analyst_notes = render_analyst_note(
        _RNG,
        disposition=narrative_key if has_curated else disposition,
        pattern=pattern_key,
        context=render_ctx,
    )
    resolution_summary = render_resolution_summary(
        disposition=narrative_key,
        actions_taken=actions,
        pattern=learnings["pattern_name"],
        compensation_myr=compensation,
    )

    cycle_id = f"CYC_{transaction_summary['timestamp'].strftime('%Y%m')}_{cid}"
    entity = (
        "acme_biz" if customer_type == "commercial" else
        ((customer.get("entities") or ["acme_paytv"])[0])
    )

    doc = {
        "_schema_version":           SCHEMA_VERSION_V4,
        "case_id":                   f"hist_{uuid.uuid4().hex[:14]}",

        # Identity.
        "customer_id":               cid,
        "customer_type":             customer_type,
        "customer_tier":             tier,
        "transaction_id":            transaction_summary["transaction_id"],
        "cycle_id":                  cycle_id,
        "entity":                    entity,

        # Frozen contexts.
        "rules_triggered":           rules_triggered,
        "transaction_summary":       transaction_summary,
        "customer_context_summary": {
            "tier":                 tier,
            "service_state":        state,
            "had_active_promotion": bool(customer.get("active_promotions")),
            "lifetime_quarantine_count": customer.get("lifetime_quarantine_count", 0),
        },

        # Outcome.
        "disposition":               disposition,
        "severity":                  severity,
        "analyst_id":                f"analyst_{_RNG.randint(1, 12):02d}",
        "analyst_notes":             analyst_notes,
        "resolution_summary":        resolution_summary,
        "resolved_at":               resolved_at,
        "resolution_time_minutes":   resolution_time_minutes,
        "resolution_path":           resolution_path,
        "actions_taken":             actions,
        "compensation_to_customer_myr": compensation,

        # Learnings.
        "learnings":                 learnings,

        # Provenance & feedback.
        "used_in_rag_count":         0,
        "last_used_in_rag_at":       None,
        "rag_relevance_feedback":    {"positive": 0, "negative": 0, "neutral": 0},
        "similar_resolved_cases":    [],

        "archived_at":               None,
    }
    # Compose the AutoEmbed payload last so the builder sees the full doc.
    doc["embed_source"] = {"text": build_history_embed_text(doc)}
    return doc


async def seed_history(db, *, count: int = 500, batch_size: int = 64) -> int:
    """Seed ``count`` historical resolved cases into ``quarantine_cases_history``.

    No application-side embedding calls — Atlas Auto Embedding picks up
    each insert via the ``case_history_autoembed_idx`` index.
    ``batch_size`` is retained for back-compat: it controls the
    ``insert_many`` batch size only.
    """
    coll = db[QUARANTINE_CASES_HISTORY]
    await coll.delete_many({})

    customer_cursor = db[CUSTOMERS_RESIDENTIAL].find(
        {},
        {
            "_id": 0,
            "customer_id":              1,
            "customer_type":            1,
            "tier":                     1,
            "address":                  1,
            "active_promotions":        1,
            "entities":                 1,
            "lifetime_quarantine_count": 1,
            "tenure_months":            1,
        },
    ).limit(2_000)
    customer_pool = [c async for c in customer_cursor]

    docs = [_build_history_doc(i, customer_pool) for i in range(count)]

    written = 0
    for start in range(0, len(docs), batch_size):
        chunk = docs[start:start + batch_size]
        await coll.insert_many(chunk, ordered=False)
        written += len(chunk)
        logger.info("history_progress", written=written, target=count)
        await asyncio.sleep(0)

    logger.info("history_seeded", count=written)
    return written
