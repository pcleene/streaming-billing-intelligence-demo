"""V3 transaction event factory (PR-3 follow-up).

Produces TransactionDocumentV3-shaped event payloads. Pure function +
deterministic with an injected `random.Random` so unit tests can assert
distribution and per-variant shape without re-running the live
simulator loop.

Distribution (master plan PR-3):
  - 70% subscription_charge
  - 20% ppv_charge        (mapped from spec's "ppv_purchase")
  -  5% refund
  -  3% termination_fee
  -  2% device_fee

Each variant emits a single canonical line item with the right
`charge_code`. Commercial-outlet customers get a different
charge_code on the subscription line (`CC_BIZ_SPORTS_PER_OUTLET`)
and skip PPV. See `app/schemas/transaction.py` for the full V3 shape.

The downstream consumer is responsible for promoting this event to
the persisted `TransactionDocumentV3` (see
`transaction_repo.insert_extref`). This factory's output is the wire
shape — totals + denormalised customer summary are computed in the repo.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime
from typing import Callable

# --- Distribution ------------------------------------------------------

# Cumulative thresholds matching the master-plan distribution. Order
# matters; the picker walks in order and returns on first hit.
_DISTRIBUTION: list[tuple[str, float]] = [
    ("subscription_charge", 0.70),
    ("ppv_charge",          0.90),  # +20%
    ("refund",              0.95),  # + 5%
    ("termination_fee",     0.98),  # + 3%
    ("device_fee",          1.00),  # + 2%
]

STATES: tuple[str, ...] = (
    "Selangor", "Kuala Lumpur", "Johor", "Penang",
    "Sabah", "Sarawak", "Perak",
)
MERCHANTS: tuple[str, ...] = (
    "acme-direct", "acme-now", "acme-go", "acme-shop",
)

# Content IDs that no customer holds entitlements for — used by the
# `entitlement` anomaly flavour to deliberately fire rule 04.
_NON_ENTITLED_POOL: tuple[str, ...] = (
    "PPV_HIDDEN_LEAK_001", "PPV_HIDDEN_LEAK_002", "PPV_HIDDEN_LEAK_003",
)
# Fallback content IDs when a customer has no entitlements; rule 04 will
# not fire on these because the rule unwinds against entitlements and
# missing entitlements yields no row to compare.
_FALLBACK_CONTENT: tuple[str, ...] = (
    "PPV_CNY_FAMILY_FILM", "PPV_EPL_DERBY_NIGHT",
    "PPV_MLBB_M5_FINAL",   "PPV_LIVERPOOL_CHELSEA",
)

# Per-flavour applicability — keeps anomalies aligned with txn types that
# can actually carry the relevant payload. Picking an anomaly that can't
# apply falls back to `huge_amount` (universal).
_ANOMALY_FLAVOURS: dict[str, tuple[str, ...]] = {
    "geo":                     ("subscription_charge", "ppv_charge", "refund", "termination_fee", "device_fee"),
    "discount":                ("subscription_charge",),
    "huge_amount":             ("subscription_charge", "ppv_charge", "refund", "termination_fee", "device_fee"),
    "earned_split":            ("subscription_charge",),
    "entitlement":             ("ppv_charge",),
    "double_ppv":              ("ppv_charge",),
    "proration":               ("subscription_charge",),
    "termination_overcharge":  ("termination_fee",),
}


def _pick_transaction_type(rng: random.Random) -> str:
    r = rng.random()
    for name, threshold in _DISTRIBUTION:
        if r < threshold:
            return name
    return _DISTRIBUTION[-1][0]


# --- Per-variant builders ---------------------------------------------


def _subscription_item(
    customer: dict, *, is_commercial: bool, rng: random.Random
) -> dict:
    sub = next(
        (s for s in (customer.get("subscriptions") or []) if s.get("status") == "active"),
        None,
    )
    monthly = float((sub or {}).get("monthly_fee_myr") or 79.90)
    code = (
        "CC_BIZ_SPORTS_PER_OUTLET" if is_commercial else "CC_SUBSCRIPTION_BASE"
    )
    return {
        "item_id": f"i_{uuid.uuid4().hex[:8]}",
        "item_type": "subscription_charge",
        "name": (sub or {}).get("package_name") or "Monthly subscription",
        "charge_code": code,
        "quantity": 1.0,
        "unit_price_myr": monthly,
        "discount_per_unit_myr": 0.0,
        "line_total_myr": monthly,
    }


def _ppv_item(customer: dict, rng: random.Random, *, content_id: str | None = None) -> dict:
    price = round(rng.uniform(9.90, 24.90), 2)
    if content_id is None:
        entitlements = customer.get("entitlements") or []
        if entitlements:
            content_id = rng.choice([e["content_id"] for e in entitlements if e.get("content_id")])
        else:
            content_id = rng.choice(_FALLBACK_CONTENT)
    return {
        "item_id": f"i_{uuid.uuid4().hex[:8]}",
        "item_type": "ppv_charge",
        "name": f"PPV title {rng.randint(1, 500)}",
        "charge_code": "CC_PPV_DIRECT",
        "content_id": content_id,
        "quantity": 1.0,
        "unit_price_myr": price,
        "discount_per_unit_myr": 0.0,
        "line_total_myr": price,
    }


def _refund_item(rng: random.Random) -> dict:
    amount = round(rng.uniform(5.0, 199.0), 2)
    return {
        "item_id": f"i_{uuid.uuid4().hex[:8]}",
        "item_type": "refund",
        "name": "Refund — billing adjustment",
        "charge_code": "CC_REFUND",
        "quantity": 1.0,
        "unit_price_myr": -amount,
        "discount_per_unit_myr": 0.0,
        "line_total_myr": -amount,
        "refund_reference_transaction_id": f"txn_orig_{uuid.uuid4().hex[:8]}",
        "refund_reason": rng.choice(
            ["service_outage", "billing_error", "good_will", "double_charge"]
        ),
    }


def _termination_item(customer: dict, rng: random.Random, *, anomalous: bool = False) -> dict:
    expected = float(customer.get("early_termination_fee_myr") or 240.0)
    # Normal: tight ±5% drift (operational noise). Anomalous: ±25-50%
    # overcharge so rule 07 has a clear, narrow signal to fire on.
    if anomalous:
        sign = rng.choice([-1.0, 1.0])
        actual = round(expected * (1.0 + sign * rng.uniform(0.25, 0.5)), 2)
    else:
        actual = round(expected * rng.uniform(0.95, 1.05), 2)
    return {
        "item_id": f"i_{uuid.uuid4().hex[:8]}",
        "item_type": "termination_fee",
        "name": "Early termination fee",
        "charge_code": "CC_TERMINATION_FEE",
        "quantity": 1.0,
        "unit_price_myr": actual,
        "discount_per_unit_myr": 0.0,
        "line_total_myr": actual,
        "computed_expected_fee_myr": expected,
        "expected_vs_billed_delta_myr": round(actual - expected, 2),
    }


def _device_fee_item(rng: random.Random) -> dict:
    price = round(rng.uniform(15.0, 60.0), 2)
    return {
        "item_id": f"i_{uuid.uuid4().hex[:8]}",
        "item_type": "device_fee",
        "name": "Decoder rental",
        "charge_code": "CC_DEVICE_FEE",
        "quantity": 1.0,
        "unit_price_myr": price,
        "discount_per_unit_myr": 0.0,
        "line_total_myr": price,
    }


_ITEM_BUILDERS: dict[str, Callable[..., dict]] = {
    "subscription_charge": lambda c, **kw: _subscription_item(
        c, is_commercial=kw["is_commercial"], rng=kw["rng"]
    ),
    "ppv_charge":          lambda c, **kw: _ppv_item(c, rng=kw["rng"]),
    "refund":              lambda c, **kw: _refund_item(rng=kw["rng"]),
    "termination_fee":     lambda c, **kw: _termination_item(c, rng=kw["rng"]),
    "device_fee":          lambda c, **kw: _device_fee_item(rng=kw["rng"]),
}


def _pick_anomaly_flavour(txn_type: str, rng: random.Random) -> str:
    """Pick an anomaly flavour applicable to the given transaction type.

    Falls back to `huge_amount` (universal) if no specific flavour
    applies. This keeps per-flavour rates bounded and predictable:
    each applicable flavour fires at roughly
        anomaly_rate × P(txn_type) × (1 / count_of_applicable_flavours)
    per emitted event.
    """
    applicable = [f for f, types in _ANOMALY_FLAVOURS.items() if txn_type in types]
    return rng.choice(applicable) if applicable else "huge_amount"


# --- Public API --------------------------------------------------------


def build_v3_event_payload(
    customer: dict,
    *,
    ts: datetime,
    anomaly: bool = False,
    rng: random.Random | None = None,
    force_type: str | None = None,
) -> dict:
    """Build a single V3 transaction event for the given customer.

    Parameters
    ----------
    customer
        A customer document (residential or commercial) — the factory
        only reads `customer_id`, `customer_type`, `address.state`,
        `subscriptions[]`, `early_termination_fee_myr`. Tolerates the
        legacy single-collection shape and the PR-1 V3 shape.
    ts
        Event timestamp (used as `timestamp` and indirectly drives
        cycle resolution downstream).
    anomaly
        When True, emits a transaction that is more likely to fire a
        rule (cross-state location, oversized amount, heavy discount).
        Used by the live simulator.
    rng
        Optional seeded `random.Random` for deterministic tests. The
        global `random` module is used when omitted.
    force_type
        Bypass the distribution for tests that need a specific variant.

    Returns
    -------
    dict
        The V3 event payload — `transaction_id`, `customer_id`,
        `timestamp`, `transaction_type`, `merchant_id`, `channel`,
        `currency`, `location`, `items[]`, `discounts`, `tax`,
        `payment`, `metadata`. Totals (`subtotal_myr`, `total_myr`)
        are NOT emitted here — `transaction_repo.insert_extref`
        recomputes them from `items[]` to avoid drift between producer
        and consumer.
    """
    rng = rng or random.Random()  # noqa: S311 — non-crypto demo simulator

    is_commercial = (customer.get("customer_type") == "commercial")
    has_entitlements = bool(customer.get("entitlements"))
    if force_type is None and (is_commercial or not has_entitlements):
        # Commercial outlets don't run PPV in the demo, and residential
        # customers with no entitlements at all can't realistically
        # generate PPV charges (the rule-engine would always flag them).
        # Re-roll PPV → another type so rule 04 only fires on the
        # `entitlement` anomaly flavour.
        attempts = 0
        txn_type = _pick_transaction_type(rng)
        while txn_type == "ppv_charge" and attempts < 5:
            txn_type = _pick_transaction_type(rng)
            attempts += 1
    else:
        txn_type = force_type or _pick_transaction_type(rng)

    # Normal (non-anomaly) item build. Termination fee respects the
    # `anomalous` flag so that normal termfees stay within ±5% drift.
    if txn_type == "termination_fee":
        item = _termination_item(customer, rng, anomalous=False)
    else:
        item = _ITEM_BUILDERS[txn_type](customer, is_commercial=is_commercial, rng=rng)
    items = [item]

    # Anomaly flavours — picked per-txn-type so applicability is enforced.
    discounts: list[dict] = []
    metadata: dict = {}
    home_state = (customer.get("address") or {}).get("state") or rng.choice(STATES)
    txn_state = home_state
    flavour = _pick_anomaly_flavour(txn_type, rng) if anomaly else None

    if flavour == "geo":
        txn_state = rng.choice([s for s in STATES if s != home_state])
    elif flavour == "discount":
        discount_amount = round(item["line_total_myr"] * rng.uniform(0.4, 0.8), 2)
        item["discount_per_unit_myr"] = discount_amount
        item["line_total_myr"] = round(item["line_total_myr"] - discount_amount, 2)
        discounts = [
            {
                "promo_id": "PROMO_ANOMALY",
                "description": "Suspiciously generous one-off",
                "applies_to_item_ids": [item["item_id"]],
                "amount_myr": discount_amount,
            }
        ]
    elif flavour == "huge_amount":
        blow_up = round(rng.uniform(2000.0, 5000.0), 2)
        item["unit_price_myr"] = blow_up
        item["line_total_myr"] = blow_up
    elif flavour == "entitlement":
        # PPV against a content id the customer does not hold an
        # entitlement for. Rule 04 fires on this.
        item["content_id"] = rng.choice(_NON_ENTITLED_POOL)
    elif flavour == "double_ppv":
        # Two charges for the same content_id under different charge
        # codes, in the SAME event. Rule 09 fires when items[] carries
        # two distinct charge codes for one content_id.
        bundle = dict(item)
        bundle["item_id"] = f"i_{uuid.uuid4().hex[:8]}"
        bundle["charge_code"] = "CC_BUNDLE_PPV"
        bundle["unit_price_myr"] = round(item["unit_price_myr"] * 0.5, 2)
        bundle["line_total_myr"] = bundle["unit_price_myr"]
        items.append(bundle)
    elif flavour == "termination_overcharge":
        # Replace the termination item with an out-of-band overcharge.
        items = [_termination_item(customer, rng, anomalous=True)]
        item = items[0]

    # metadata payload — earned/unearned split is the steady-state norm
    # for subscription_charge; `earned_split` anomaly perturbs it.
    if txn_type == "subscription_charge":
        amt = float(item["line_total_myr"])
        earned   = round(amt * 0.5, 2)
        unearned = round(amt - earned, 2)
        if flavour == "earned_split":
            # break the invariant by ~10-20% of amount
            drift = round(amt * rng.uniform(0.10, 0.20), 2)
            unearned = round(unearned + drift, 2)
        metadata["earned_amount_myr"]   = earned
        metadata["unearned_amount_myr"] = unearned

        # proration metadata: 5% of subscription_charge txns are
        # mid-cycle changes by default; the `proration` anomaly
        # perturbs the actual-vs-expected delta.
        is_mid_cycle = flavour == "proration" or rng.random() < 0.05
        if is_mid_cycle:
            amt2 = float(item["line_total_myr"])
            expected_prop = round(amt2 * 0.5, 2)  # half-period default
            if flavour == "proration":
                actual_prop = round(expected_prop * rng.uniform(1.10, 1.40), 2)
            else:
                actual_prop = round(expected_prop * rng.uniform(0.99, 1.01), 2)
            metadata["is_mid_cycle_change"]    = True
            metadata["proration_amount_myr"]   = actual_prop
            metadata["expected_proration_myr"] = expected_prop

    subtotal = sum(float(i["line_total_myr"]) for i in items)
    tax_amount = round(max(0.0, subtotal) * 0.06, 2)  # SST 6%

    event: dict = {
        "transaction_id": f"txn_{uuid.uuid4().hex[:16]}",
        "customer_id": customer["customer_id"],
        "timestamp": ts,
        "transaction_type": txn_type,
        "channel": "auto_debit" if txn_type == "subscription_charge" else "self_serve",
        "merchant_id": rng.choice(MERCHANTS),
        "currency": "MYR",
        "location": {"state": txn_state, "country": "MY"},
        "items": items,
        "discounts": discounts,
        "tax": {"rate": 0.06, "name": "SST", "amount_myr": tax_amount},
        "payment": {"method": "auto_debit", "auto_debit": True}
            if txn_type == "subscription_charge"
            else {"method": "credit_card", "auto_debit": False},
        "metadata": metadata,
    }
    return event


# Distribution constants kept exported for tests.
DISTRIBUTION_TARGETS: dict[str, float] = {
    "subscription_charge": 0.70,
    "ppv_charge":          0.20,
    "refund":              0.05,
    "termination_fee":     0.03,
    "device_fee":          0.02,
}
