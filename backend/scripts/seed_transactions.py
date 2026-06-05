"""Seed transactions in the V3 extended-reference shape.

Writes documents matching `TransactionDocumentV3` to the `transactions`
collection: full identity routing (customer_id, customer_type, account_id,
parent_account_id, outlet_id, cycle_id), `bill_period` anchors, denormalised
`customer_summary` frozen at write time, structured `items` with `charge_code`,
`payment`, `loyalty`, `campaign_attribution`, and `computed_signals`. Stream
rule pipelines and the feature engineer read straight from this document —
no $lookup.

Reads BOTH `customers_residential` and `customers_commercial` so both
customer types receive transaction histories.

Updates `recent_transactions` on each customer (capped at 50) using the
`RecentTransactionEmbed` shape so the Customer 360 view sees a fresh
preview.

Anomaly sprinkles (preserved from the legacy seeder):
  - geographic anomalies (~2%)
  - duplicate burst (~1%, near-zero gap)
  - discount mismatch / discount-without-promo (~1.5%)
  - amount outliers (~1%)
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    SCHEMA_VERSION_V3,
    TRANSACTIONS,
)
from app.core.logging import get_logger
from scripts._realism import (
    pick_addon,
    pick_ppv_title,
)

logger = get_logger(__name__)

_rng = random.Random(43)

# --- Pools ---------------------------------------------------------------

MERCHANTS: tuple[str, ...] = ("acme-direct", "acme-now", "acme-go", "acme-shop")

# Channel × transaction type matrix (V3 enum supersets the legacy one).
CHANNELS: tuple[str, ...] = (
    "self_serve_app", "ivr", "auto_debit", "billing_run",
    "field_agent", "retail_pos",
)

RES_TXN_TYPES: tuple[str, ...] = (
    "subscription_charge", "ppv_charge", "addon_charge",
    "billing_adjustment", "device_fee", "late_fee", "promo_rebate",
)
COM_TXN_TYPES: tuple[str, ...] = (
    "subscription_charge", "addon_charge", "device_fee",
    "billing_adjustment", "late_fee",
)

# Service-zone lookup (mirrors the customer seeder pools).
_SERVICE_ZONES: dict[str, str] = {
    "Selangor":         "ZONE_KLG",
    "Kuala Lumpur":     "ZONE_KL",
    "Johor":            "ZONE_JHR",
    "Penang":           "ZONE_PEN",
    "Perak":            "ZONE_PRK",
    "Sabah":            "ZONE_SBH",
    "Sarawak":          "ZONE_SWK",
    "Negeri Sembilan":  "ZONE_NSN",
    "Kedah":            "ZONE_KDH",
    "Pahang":           "ZONE_PHG",
}

# Charge-code catalog (mirror of CHARGE_CODES seeded elsewhere).
_CHARGE_CODES: dict[str, str] = {
    "subscription_charge": "CC_SUB_MTHLY",
    "ppv_charge":          "CC_PPV_STD",
    "addon_charge":        "CC_ADDON_STD",
    "termination_fee":     "CC_ETF",
    "device_fee":          "CC_DEVICE_RENT",
    "late_fee":            "CC_LATE",
    "refund":              "CC_REFUND",
    "billing_adjustment":  "CC_ADJ",
    "promo_rebate":        "CC_PROMO_REBATE",
    "device_purchase":     "CC_DEVICE_PUR",
}

PAYMENT_METHODS: tuple[str, ...] = (
    "credit_card", "auto_debit", "fpx", "ewallet", "post_paid_invoice",
)
CARD_NETWORKS: tuple[str, ...] = ("visa", "mastercard", "amex", "unionpay")


# --- Helpers -------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_bill_period(ts: datetime) -> dict:
    """Anchor the period to the calendar month containing `ts`."""
    cycle_start = ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_start = (cycle_start + timedelta(days=32)).replace(day=1)
    cycle_end = next_start - timedelta(seconds=1)
    return {
        "start":              cycle_start,
        "end":                cycle_end,
        "cycle_length_days":  (cycle_end - cycle_start).days + 1,
        "bill_day_of_month":  1,
    }


def _build_cycle_id(ts: datetime, customer_id: str) -> str:
    return f"CYC_{ts.strftime('%Y%m')}_{customer_id}"


def _customer_summary(c: dict) -> dict:
    """Frozen snapshot of the customer at billing time (V3 flat-root reads)."""
    name = c.get("name") or c.get("customer_id", "")
    state = (c.get("address") or {}).get("state")
    subs = c.get("subscriptions") or []
    pkg_at_billing = None
    if subs:
        s0 = subs[0]
        pkg_at_billing = {
            "package_code":    s0.get("package_code"),
            "package_name":    s0.get("package_name"),
            "monthly_fee_myr": s0.get("monthly_fee_myr"),
        }
    promos = []
    for p in c.get("active_promotions") or []:
        promos.append({
            "promotion_code":      p.get("promotion_code"),
            "description":         p.get("description"),
            "discount_pct":        p.get("discount_pct"),
            "discount_amount_myr": p.get("discount_amount_myr"),
        })
    entitlements = [
        e.get("content_id") for e in (c.get("entitlements") or []) if e.get("content_id")
    ]

    summary: dict = {
        "name":                            name,
        "tier":                            c.get("tier"),
        "ic_number":                       c.get("ic_number"),
        "service_state":                   state,
        "package_at_billing":              pkg_at_billing,
        "active_promotions_at_billing":    promos,
        "active_entitlements_at_billing":  entitlements,
        "loyalty_tier_at_billing":         c.get("tier"),
        "loyalty_member_id":               None,
    }
    if c.get("customer_type") == "commercial":
        bp = c.get("business_profile") or {}
        summary["parent_business_name"] = bp.get("business_name")
        summary["outlet_label"] = bp.get("outlet_label")
        summary["venue_capacity"] = bp.get("venue_capacity")
    return summary


def _build_location(c: dict, *, override_state: str | None = None) -> dict:
    """Construct TransactionLocation, optionally with a foreign state to
    inject a geographic-anomaly sample."""
    addr = c.get("address") or {}
    home_state = override_state or addr.get("state") or "Selangor"
    point = addr.get("location")
    if override_state and override_state != addr.get("state"):
        # When we synthesise an anomaly we drop the original point so the
        # rule pipeline notices the location row but the geo-distance check
        # falls back to its default.
        point = None
    return {
        "state":        home_state,
        "city":         addr.get("city"),
        "service_zone": _SERVICE_ZONES.get(home_state),
        "point":        point,
    }


def _pick_payment(rng: random.Random) -> dict:
    method = rng.choice(PAYMENT_METHODS)
    payment: dict = {
        "method":         method,
        "card_last4":     None,
        "card_network":   None,
        "auto_debit":     method == "auto_debit",
        "installment_months": None,
        "auth_code":      None,
        "settled_at":     None,
        "psp":            None,
        "psp_reference":  None,
    }
    if method == "credit_card":
        payment["card_last4"] = f"{rng.randint(0, 9999):04d}"
        payment["card_network"] = rng.choice(CARD_NETWORKS)
        payment["auth_code"] = uuid.uuid4().hex[:8].upper()
    return payment


def _build_loyalty(rng: random.Random, *, tier: str | None) -> dict:
    earned = round(rng.uniform(0, 25), 1)
    return {
        "points_earned":        earned,
        "points_redeemed":      0.0,
        "tier_at_purchase":     tier,
        "member_id":            None,
        "bonus_multiplier":     1.0 if tier in (None, "bronze") else 1.5,
        "points_balance_after": earned + rng.uniform(0, 500),
    }


def _build_item(
    rng: random.Random,
    *,
    txn_type: str,
    base_amount: float,
    discount_per_unit: float,
    pkg: dict | None,
) -> dict:
    qty = 1.0
    unit_price = round(base_amount, 2)

    # Resolve a realistic product_ref/name/subcategory from realism pools
    # for PPV and add-on lines; subscription lines stay anchored to the
    # customer's `subscriptions[0]` package; everything else falls back to
    # a humanised txn_type label.
    product_ref: str | None = None
    name: str
    subcategory: str | None = None
    if txn_type == "subscription_charge" and pkg:
        product_ref = pkg["package_code"]
        name = pkg["package_name"]
        subcategory = "subscription"
    elif txn_type == "ppv_charge":
        content_id, title, ppv_price, ppv_cat = pick_ppv_title(rng)
        product_ref = content_id
        name = title
        subcategory = ppv_cat
        unit_price = round(ppv_price, 2)
    elif txn_type == "addon_charge":
        addon_code, addon_name, partner, addon_price = pick_addon(rng)
        product_ref = addon_code
        name = addon_name
        subcategory = partner
        unit_price = round(addon_price, 2)
    else:
        name = txn_type.replace("_", " ").title()

    line_total = round((unit_price - discount_per_unit) * qty, 2)
    item: dict = {
        "item_id":               f"itm_{uuid.uuid4().hex[:10]}",
        "item_type":             txn_type if txn_type != "ppv_purchase" else "ppv_charge",
        "product_ref":           product_ref,
        "name":                  name,
        "category":              "subscription" if txn_type == "subscription_charge" else txn_type,
        "subcategory":           subcategory,
        "brand":                 "Acme",
        "billing_period_days":   30 if txn_type == "subscription_charge" else None,
        "quantity":              qty,
        "unit_price_myr":        unit_price,
        "discount_per_unit_myr": round(discount_per_unit, 2),
        "line_total_myr":        line_total,
        "promo_id":              None,
        "charge_code":           _CHARGE_CODES.get(txn_type, "CC_GEN"),
        "loyalty_points_earned": round(rng.uniform(0, 5), 1),
        "tax_inclusive":         False,
        "computed_expected_fee_myr":   None,
        "expected_vs_billed_delta_myr": None,
        "refund_reference_transaction_id": None,
        "refund_reason":          None,
        "metadata":               None,
    }
    return item


def _txns_for_customer(
    customer: dict, *, n: int, rng: random.Random,
) -> list[dict]:
    """Generate a list of TransactionDocumentV3-shaped transactions."""
    cid = customer["customer_id"]
    ctype = customer.get("customer_type", "residential")
    account_id = customer.get("account_id") or f"ACC_{cid}"
    outlet_id = customer.get("outlet_id")
    parent_account_id = customer.get("parent_account_id")
    tier = customer.get("tier") or "silver"

    # Choose a "home state" + service zone (V3 flat-root address).
    home_state = (customer.get("address") or {}).get("state") or "Selangor"

    # Base spend by tier.
    base_amount = {
        "bronze": 45.0, "silver": 75.0, "gold": 120.0, "platinum": 180.0,
    }.get(tier, 75.0)
    if ctype == "commercial":
        base_amount *= 2.5

    active_promos = customer.get("active_promotions") or []
    has_promo = bool(active_promos)
    primary_promo_code = (
        active_promos[0].get("promotion_code") if active_promos else None
    )
    primary_promo_desc = (
        active_promos[0].get("description") if active_promos else None
    )
    pkg = None
    subs = customer.get("subscriptions") or []
    if subs:
        s0 = subs[0]
        pkg = {
            "package_code": s0.get("package_code"),
            "package_name": s0.get("package_name"),
        }

    types_pool = COM_TXN_TYPES if ctype == "commercial" else RES_TXN_TYPES

    # `n` is now passed in by the orchestrator from a power-law draw
    # (heavy tail), so per-customer variance lives there instead of
    # being layered on top of a flat base.
    customer_summary = _customer_summary(customer)
    out: list[dict] = []
    now = _utcnow()

    for _ in range(n):
        ts = now - timedelta(minutes=rng.randint(1, 60 * 24 * 90))
        amount = round(base_amount * rng.uniform(0.6, 1.6), 2)
        ttype = rng.choice(types_pool)
        merchant = rng.choice(MERCHANTS)
        channel = rng.choice(CHANNELS)
        state = home_state
        discount = 0.0

        # Anomaly sprinkles
        roll = rng.random()
        anomaly_geo = False
        if roll < 0.02:  # geographic
            state = rng.choice([
                s for s in ("Sabah", "Sarawak", "Penang", "Johor")
                if s != home_state
            ])
            anomaly_geo = True
        if not has_promo and roll < 0.015:  # discount mismatch
            discount = round(amount * rng.uniform(0.4, 0.7), 2)
        if roll < 0.01:  # amount outlier
            amount = round(amount * rng.uniform(8.0, 25.0), 2)

        item = _build_item(
            rng,
            txn_type=ttype,
            base_amount=amount,
            discount_per_unit=discount,
            pkg=pkg if ttype == "subscription_charge" else None,
        )
        subtotal = round(item["unit_price_myr"] * item["quantity"], 2)
        total_discount = round(item["discount_per_unit_myr"] * item["quantity"], 2)
        tax_amount = round((subtotal - total_discount) * 0.06, 2)  # SST 6%
        total = round(subtotal - total_discount + tax_amount, 2)

        cycle_id = _build_cycle_id(ts, cid)

        location = _build_location(
            customer,
            override_state=state if anomaly_geo else None,
        )

        txn = {
            "_schema_version":   SCHEMA_VERSION_V3,
            "transaction_id":    f"txn_{uuid.uuid4().hex[:16]}",

            "customer_id":       cid,
            "customer_type":     ctype,
            "account_id":        account_id,
            "outlet_id":         outlet_id,
            "parent_account_id": parent_account_id,
            "cycle_id":          cycle_id,
            "bill_period":       _build_bill_period(ts),

            "timestamp":         ts,
            "transaction_type":  ttype,
            "channel":           channel,
            "entity":            "acme_paytv" if ctype == "residential" else "acme_biz",
            "merchant_id":       merchant,
            "currency":          "MYR",

            "location":          location,

            "customer_summary":  customer_summary,

            "items":             [item],

            "subtotal_myr":      subtotal,
            "discounts":         (
                [{
                    "promo_id":              primary_promo_code or "PROMO_GOODWILL",
                    "description":           primary_promo_desc or "Goodwill discount applied",
                    "applies_to_item_ids":   [item["item_id"]],
                    "amount_myr":            total_discount,
                }] if total_discount > 0 else []
            ),
            "total_discount_myr": total_discount,
            "tax": {
                "rate":       0.06,
                "name":       "SST",
                "amount_myr": tax_amount,
            },
            "total_myr":         total,

            "payment":           _pick_payment(rng),
            "loyalty":           _build_loyalty(rng, tier=tier),

            "quarantined":       False,
            "quarantine_case_ids": [],

            "campaign_attribution": None,

            "is_returned":       False,
            "is_refund":         ttype == "refund",

            "computed_signals": {
                "amount_vs_avg_30d_pct":        round(rng.uniform(-0.5, 2.5), 3),
                "discount_pct_of_subtotal":     round(
                    (total_discount / subtotal) if subtotal else 0.0, 3
                ),
                "is_first_ppv":                 ttype == "ppv_charge" and rng.random() < 0.2,
                "is_lock_in_period":            rng.random() < 0.4,
                "is_promo_active":              has_promo,
                "geographic_distance_from_home_km": (
                    round(rng.uniform(150.0, 1500.0), 1) if anomaly_geo else
                    round(rng.uniform(0.0, 25.0), 1)
                ),
                "txn_velocity_5m":              rng.randint(0, 3),
                "termination_fee_pct_of_expected": None,
            },

            # Stream / source metadata (alias-prefixed in DB).
            "_ingested_at":     _utcnow(),
            "_source_topic":    "seed.transactions",
            "_source_partition": None,
            "_source_offset":   None,
            "_event_id":        uuid.uuid4().hex,
        }
        out.append(txn)

        # Duplicate burst (~1%) — same key, near-zero gap
        if rng.random() < 0.01:
            for _k in range(rng.randint(2, 4)):
                dup = dict(txn)
                dup["transaction_id"] = f"txn_{uuid.uuid4().hex[:16]}"
                dup["timestamp"] = ts + timedelta(seconds=rng.randint(1, 30))
                dup["_event_id"] = uuid.uuid4().hex
                out.append(dup)

    return out


# --- Customer iteration --------------------------------------------------

# Projection used when fetching customers — all fields needed to construct
# the frozen `customer_summary` and routing identity.
_CUSTOMER_PROJECTION = {
    "_id": 0,
    "customer_id":         1,
    "customer_type":       1,
    "name":                1,
    "account_id":          1,
    "parent_account_id":   1,
    "outlet_id":           1,
    "tier":                1,
    "ic_number":           1,
    "address":             1,
    "subscriptions":       1,
    "active_promotions":   1,
    "entitlements":        1,
    "business_profile":    1,
}


async def _stream_customers(db):
    """Yield V3 customer docs from both residential and commercial collections."""
    for coll_name in (CUSTOMERS_RESIDENTIAL, CUSTOMERS_COMMERCIAL):
        coll = db[coll_name]
        cursor = coll.find({}, _CUSTOMER_PROJECTION)
        async for c in cursor:
            yield c


# --- Public entrypoint --------------------------------------------------

def _power_law_counts(
    n_customers: int,
    target_total: int,
    *,
    alpha: float,
    cap: int,
    rng: random.Random,
) -> list[int]:
    """Allocate per-customer transaction counts via a Pareto/Lomax draw.

    Heavy-tailed: most customers see a small number of transactions, a
    handful see many. Counts are scaled so the global sum lands near
    `target_total` and individually clamped at `cap` to keep the tail
    realistic. A floor of 1 is *not* enforced — a sliver of customers
    legitimately have zero transactions in the demo window.
    """
    if n_customers <= 0 or target_total <= 0:
        return []
    raw = [(1.0 - rng.random()) ** (-1.0 / alpha) - 1.0 for _ in range(n_customers)]
    s = sum(raw) or 1.0
    scale = target_total / s
    counts = [min(cap, int(round(x * scale))) for x in raw]
    # Top up the largest buckets if rounding/clamping left us short of
    # target. Cheap O(target_total) loop that converges within a couple
    # of passes for sane inputs.
    deficit = target_total - sum(counts)
    if deficit > 0:
        order = sorted(range(n_customers), key=lambda i: counts[i], reverse=True)
        i = 0
        guard = 0
        while deficit > 0 and guard < 4 * n_customers:
            idx = order[i % n_customers]
            if counts[idx] < cap:
                counts[idx] += 1
                deficit -= 1
            i += 1
            guard += 1
    return counts


async def seed_transactions(
    db,
    *,
    target_total: int = 30_000,
    alpha: float = 1.5,
    max_per_customer: int = 60,
) -> int:
    """Seed `transactions` (V3 shape) using a power-law per-customer
    distribution and refresh each customer's `recent_transactions`
    preview.

    `target_total` is the approximate global transaction count; the
    actual written count drifts ~1-2% above due to the duplicate-burst
    sprinkle inside `_txns_for_customer`.

    Returns the total number of transactions written.
    """
    txn_coll = db[TRANSACTIONS]
    res_coll = db[CUSTOMERS_RESIDENTIAL]
    com_coll = db[CUSTOMERS_COMMERCIAL]

    await txn_coll.delete_many({})

    rng = random.Random(43)

    # Materialize customers so we can pre-allocate counts. The projection
    # keeps each doc small (~1-2KB) so 10k customers ≈ ~15MB — fine.
    customers: list[dict] = []
    async for c in _stream_customers(db):
        customers.append(c)

    counts = _power_law_counts(
        len(customers),
        target_total,
        alpha=alpha,
        cap=max_per_customer,
        rng=rng,
    )
    logger.info(
        "transactions_distribution",
        n_customers=len(customers),
        target_total=target_total,
        alpha=alpha,
        max_per_customer=max_per_customer,
        zero_count_customers=sum(1 for n in counts if n == 0),
        max_count=max(counts) if counts else 0,
    )

    written = 0
    BATCH = 5_000
    pending: list[dict] = []

    for c, n in zip(customers, counts):
        if n <= 0:
            continue
        txns = _txns_for_customer(c, n=n, rng=rng)
        pending.extend(txns)
        if len(pending) >= BATCH:
            await txn_coll.insert_many(pending, ordered=False)
            written += len(pending)
            pending.clear()
            if written % (BATCH * 10) == 0:
                logger.info(
                    "transactions_progress",
                    written=written,
                    target=target_total,
                )
    if pending:
        await txn_coll.insert_many(pending, ordered=False)
        written += len(pending)

    # Refresh `recent_transactions` previews on customer docs (≤50 entries
    # per RecentTransactionEmbed shape: transaction_id/timestamp/
    # transaction_type/total_myr/total_discount_myr/merchant_id/quarantined).
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$customer_id",
            "preview": {
                "$push": {
                    "transaction_id":     "$transaction_id",
                    "timestamp":          "$timestamp",
                    "transaction_type":   "$transaction_type",
                    "total_myr":          "$total_myr",
                    "total_discount_myr": "$total_discount_myr",
                    "merchant_id":        "$merchant_id",
                    "quarantined":        "$quarantined",
                },
            },
        }},
        {"$project": {"preview": {"$slice": ["$preview", 50]}}},
    ]
    async for row in await txn_coll.aggregate(pipeline):
        await res_coll.update_one(
            {"customer_id": row["_id"]},
            {"$set": {"recent_transactions": row["preview"]}},
        )
        await com_coll.update_one(
            {"customer_id": row["_id"]},
            {"$set": {"recent_transactions": row["preview"]}},
        )

    logger.info("transactions_seeded", count=written)
    return written
