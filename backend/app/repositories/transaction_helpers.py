"""Pure helpers for transaction persistence and read-side aggregates.

Used by `transaction_repo` for V3 extended-reference writes and
`customer_pattern` summaries. Keeps the repository module focused on I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

PPV_TYPES = {"ppv_charge", "ppv_purchase"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_recent_embed(txn: dict) -> dict:
    """Project a V3 transaction doc to the RecentTransactionEmbed shape."""
    return {
        "transaction_id": txn.get("transaction_id"),
        "timestamp": txn.get("timestamp"),
        "transaction_type": txn.get("transaction_type"),
        "total_myr": float(txn.get("total_myr") or 0.0),
        "total_discount_myr": float(txn.get("total_discount_myr") or 0.0),
        "merchant_id": txn.get("merchant_id"),
        "quarantined": bool(txn.get("quarantined", False)),
    }


def summarise_customer_pattern(
    rows: list[dict], *, customer_id: str, window_days: int
) -> dict:
    """Build the ``customer_pattern`` shape from raw txn docs.

    Pure function — kept module-level for direct unit testability and
    so the agent's offline tests can reuse it without spinning a repo.

    Mirrors a Mongo ``$facet`` of stats + top-3 charge codes:
    - txn_count, amount mean/stddev (population), first/last timestamp
    - top 3 charge_codes by occurrence (each line item counts once)

    Reads ``total_myr`` when present; falls back to legacy ``amount``.
    """
    if not rows:
        return {
            "customer_id": customer_id,
            "window_days": window_days,
            "txn_count": 0,
            "amount_mean_myr": 0.0,
            "amount_stddev_myr": 0.0,
            "top_charge_codes": [],
            "first_txn_at": None,
            "last_txn_at": None,
        }
    amounts: list[float] = []
    timestamps: list[datetime] = []
    code_counts: dict[str, int] = {}
    for row in rows:
        amt = row.get("total_myr")
        if amt is None:
            amt = row.get("amount")
        amounts.append(float(amt or 0.0))
        ts = row.get("timestamp")
        if isinstance(ts, datetime):
            timestamps.append(ts)
        for item in (row.get("items") or []):
            code = item.get("charge_code")
            if code:
                code_counts[code] = code_counts.get(code, 0) + 1
    n = len(amounts)
    mean = sum(amounts) / n if n else 0.0
    if n > 1:
        variance = sum((a - mean) ** 2 for a in amounts) / n  # population
        stddev = variance ** 0.5
    else:
        stddev = 0.0
    top = sorted(code_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {
        "customer_id": customer_id,
        "window_days": window_days,
        "txn_count": n,
        "amount_mean_myr": round(mean, 2),
        "amount_stddev_myr": round(stddev, 2),
        "top_charge_codes": [
            {"charge_code": code, "count": count} for code, count in top
        ],
        "first_txn_at": min(timestamps) if timestamps else None,
        "last_txn_at": max(timestamps) if timestamps else None,
    }


def coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        # Tolerate the simulator's `.isoformat()` output.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"unparseable timestamp: {value!r}")


def resolve_cycle(
    cycle_doc: dict | None, customer_id: str, ts: datetime
) -> tuple[str, dict]:
    """Return (cycle_id, bill_period) for the V3 transaction doc.

    PR-3 read-only behaviour: if a bill_cycle covers `ts`, use it. Else
    synthesise a deterministic cycle_id keyed on (customer_id, YYYYMM)
    so the demo path keeps working before PR-4 wires the create-on-miss.
    """
    if cycle_doc is not None:
        anchors = cycle_doc.get("cycle_anchors") or {}
        return cycle_doc["cycle_id"], {
            "start": anchors.get("cycle_start"),
            "end": anchors.get("cycle_end"),
            "cycle_length_days": anchors.get("cycle_length_days") or 30,
            "bill_day_of_month": anchors.get("bill_day_of_month") or 1,
        }
    # Synthesised cycle — covers the calendar month containing `ts`.
    year = ts.year
    month = ts.month
    start = datetime(year, month, 1, tzinfo=ts.tzinfo or timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=ts.tzinfo or timezone.utc) - timedelta(seconds=1)
    else:
        end = datetime(year, month + 1, 1, tzinfo=ts.tzinfo or timezone.utc) - timedelta(seconds=1)
    cycle_id = f"cyc_{customer_id}_{year:04d}{month:02d}"
    return cycle_id, {
        "start": start,
        "end": end,
        "cycle_length_days": 30,
        "bill_day_of_month": 1,
    }


def project_customer_summary(customer: dict, ctype: str) -> dict:
    """Build the CustomerSummary embed from a flat-root V3 customer doc."""
    name = customer.get("name") or "Unknown"

    subs = customer.get("subscriptions") or []
    active_sub = next((s for s in subs if (s.get("status") in ("active", None))), None)
    package_at_billing = (
        {
            "package_code": active_sub.get("package_code"),
            "package_name": active_sub.get("package_name"),
            "monthly_fee_myr": active_sub.get("monthly_fee_myr"),
        }
        if active_sub
        else None
    )
    promotions = [
        {
            "promotion_code": p.get("promotion_code"),
            "discount_pct": p.get("discount_pct"),
            "discount_amount_myr": p.get("discount_amount_myr"),
        }
        for p in (customer.get("active_promotions") or [])
    ]
    entitlements = [
        e.get("content_id")
        for e in (customer.get("entitlements") or [])
        if e.get("content_id")
    ]

    address = customer.get("address") or {}
    biz = customer.get("business_profile") or {}

    summary = {
        "name": name,
        "tier": customer.get("tier"),
        "ic_number": customer.get("ic_number"),
        "service_state": (address.get("state") if isinstance(address, dict) else None),
        "package_at_billing": package_at_billing,
        "active_promotions_at_billing": promotions,
        "active_entitlements_at_billing": entitlements,
        "loyalty_tier_at_billing": customer.get("loyalty_tier"),
        "loyalty_member_id": customer.get("loyalty_member_id"),
    }
    if ctype == "commercial":
        summary["parent_business_name"] = biz.get("business_name")
        summary["outlet_label"] = biz.get("outlet_label") or customer.get("outlet_label")
        summary["venue_capacity"] = biz.get("venue_capacity")
    return summary


def is_lock_in_period(customer: dict) -> bool:
    lock_until = customer.get("lock_in_until")
    if isinstance(lock_until, str):
        try:
            lock_until = datetime.fromisoformat(lock_until.replace("Z", "+00:00"))
        except ValueError:
            return False
    if isinstance(lock_until, datetime):
        if lock_until.tzinfo is None:
            lock_until = lock_until.replace(tzinfo=timezone.utc)
        return lock_until > utcnow()
    return False


def compute_signals(
    event: dict, customer: dict, items: list[dict], total_myr: float
) -> dict:
    """Compute the ComputedSignals embed.

    PR-3 baseline: cheap, dependency-free signals derived from cached
    customer + event data. Per ADR-022, rule pipelines may consume these
    without re-deriving from history. PR-6 / feature_engineer will keep
    the same shape.
    """
    feats = customer.get("latest_features") or {}
    avg_30d = float(feats.get("avg_amount_30d") or 0.0)
    if avg_30d > 0:
        amount_vs_avg = ((total_myr - avg_30d) / avg_30d) * 100.0
    else:
        amount_vs_avg = 0.0

    subtotal = sum(float(i.get("line_total_myr") or 0.0) for i in items) or total_myr
    total_discount = sum(
        float(d.get("amount_myr") or 0.0)
        for d in (event.get("discounts") or [])
    )
    discount_pct = (total_discount / subtotal * 100.0) if subtotal else 0.0

    is_ppv = event.get("transaction_type") in PPV_TYPES
    has_prior_ppv = any(
        (t.get("transaction_type") in PPV_TYPES)
        for t in (customer.get("recent_transactions") or [])
    )

    home_state = (customer.get("address") or {}).get("state")
    txn_state = (event.get("location") or {}).get("state")
    geo_distance_km = 0.0 if (not home_state or home_state == txn_state) else 200.0

    velocity = int(feats.get("txn_velocity_5m") or 0)

    signals = {
        "amount_vs_avg_30d_pct": round(amount_vs_avg, 2),
        "discount_pct_of_subtotal": round(discount_pct, 2),
        "is_first_ppv": is_ppv and not has_prior_ppv,
        "is_lock_in_period": is_lock_in_period(customer),
        "is_promo_active": bool(customer.get("active_promotions") or []),
        "geographic_distance_from_home_km": geo_distance_km,
        "txn_velocity_5m": velocity,
        "termination_fee_pct_of_expected": None,
    }
    if event.get("transaction_type") == "termination_fee":
        expected = float(customer.get("early_termination_fee_myr") or 0.0)
        signals["termination_fee_pct_of_expected"] = (
            round((total_myr / expected) * 100.0, 2) if expected else None
        )
    return signals
