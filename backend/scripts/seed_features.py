"""Seed the `features` collection with ~250 demo customer feature docs (PR-14).

Every doc emitted by this seeder follows the canonical schema defined
in `app.schemas.feature.canonical_feature_doc` — same flat top-level
shape as the FE worker and Spark batch writers. Demo-warm-start values
are filled where the seeder has plausible knowledge:

  * 5-min rolling counters (`txn_count_5m`, `amount_sum_5m`,
    `discount_sum_5m`) — synthetic, low-confidence.
  * 24h / 7d windows — synthetic warm-start values; the Spark
    `windows-merge-to-mongo` cell will overwrite these with real
    Delta-derived values once the streaming pipeline runs.
  * Profile-derived (`package_value_myr`, `spend_to_package_ratio`) —
    read from `customers_residential.unified_profile.financial.*` when
    available, else seeded synthetic.
  * 30-day windows + model_score / scored_at — left at `None`. These
    are owned exclusively by Spark batch / the iforest scorer.

Every seeded doc is tagged with:
  * `quality.computed_via = "seed"` — origin marker so downstream
    tooling can tell synthetic vs production data apart.
  * `quality.confidence    = 0.5` — low; these are demo warm-start
    placeholders, not measured features.
  * `_seed_marker = "pr14"` — so this script can wipe-and-re-seed
    cleanly across runs.

Run via:
    python -m scripts.seed_features
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.constants import CUSTOMERS_RESIDENTIAL, FEATURES
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.schemas.feature import (
    CANONICAL_FEATURE_FIELDS,
    FEATURE_SET_VERSION_CURRENT,
    canonical_feature_doc,
)

logger = get_logger(__name__)


SEED_MARKER = "pr14"
FEATURE_SET_VERSION = FEATURE_SET_VERSION_CURRENT

# Mirrors `app.workers.feature_drift_detector.TRACKED_FEATURES` — kept as a
# local copy so this seed script can be imported without dragging the worker
# module's runtime imports into the script's import graph.
TRACKED_FEATURES: tuple[str, ...] = (
    "txn_count_5m",
    "amount_sum_5m",
    "discount_sum_5m",
    "txn_count_24h",
    "spend_24h_myr",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _delete_marker(coll, marker: str) -> None:
    """Idempotent wipe of prior seed docs.

    Prefers `delete_many` (production Mongo / Motor) but falls back to a
    cursor-based delete loop so the FakeDB unit tests don't need their
    own `delete_many` implementation.
    """
    delete_many = getattr(coll, "delete_many", None)
    if callable(delete_many):
        try:
            await delete_many({"_seed_marker": marker})
            return
        except NotImplementedError:
            pass
    # FakeDB fallback: iterate via find and delete_one per match.
    docs = []
    async for d in coll.find({"_seed_marker": marker}):
        docs.append(d)
    for _ in docs:
        await coll.delete_one({"_seed_marker": marker})


async def _insert_docs(coll, docs: list[dict]) -> None:
    """Insert docs using `insert_many` if available, else a per-doc loop."""
    insert_many = getattr(coll, "insert_many", None)
    if callable(insert_many) and docs:
        try:
            await insert_many(docs, ordered=False)
            return
        except NotImplementedError:
            pass
    for d in docs:
        await coll.insert_one(d)


async def _resolve_customers(
    db, count: int, rng: random.Random,
) -> list[dict]:
    """Pull up to `count` customer rows (id + profile financials) from
    `customers_residential`.

    Returns dicts with keys: `customer_id`, `package_value_myr` (may be None
    when the profile doesn't carry a financial sub-doc).

    If the collection is empty or short, top up with synthetic ids whose
    `package_value_myr` is None — the seeder will fall back to a synthetic
    value in that case.
    """
    coll = db[CUSTOMERS_RESIDENTIAL]
    found: list[dict] = []
    cursor = coll.find(
        {},
        {
            "_id": 0,
            "customer_id": 1,
            "unified_profile.financial.total_monthly_value_myr": 1,
        },
    ).limit(count)
    async for d in cursor:
        cid = d.get("customer_id")
        if not isinstance(cid, str):
            continue
        profile = d.get("unified_profile") or {}
        financial = profile.get("financial") or {}
        pkg = financial.get("total_monthly_value_myr")
        try:
            pkg_val = float(pkg) if pkg is not None else None
        except (TypeError, ValueError):
            pkg_val = None
        found.append({"customer_id": cid, "package_value_myr": pkg_val})

    if len(found) < count:
        synth_needed = count - len(found)
        base = rng.randint(0, 1_000_000)
        for i in range(synth_needed):
            found.append({
                "customer_id": f"cust_{base + i}",
                "package_value_myr": None,
            })
    return found[:count]


def _build_feature_doc(
    cust: dict,
    rng: random.Random,
    *,
    now: datetime,
) -> dict:
    """Assemble a canonical features document for one customer.

    Starts from `canonical_feature_doc(...)` so every CANONICAL_FEATURE_FIELDS
    key is present, then overlays seeded values in place. Sub-docs
    (`lineage`, `quality`) are inherited from the canonical default.
    The 5m hopping-window fields (`txn_count_5m` etc.) are seeded with
    warm-start values; ASP `acme-feature-window-5m` overwrites them
    on the next hop close once a transaction lands for the customer.
    """
    cid = cust["customer_id"]
    doc = canonical_feature_doc(cid, now=now)

    as_of = now - timedelta(seconds=rng.randint(0, 24 * 3600))
    first_txn = as_of - timedelta(days=rng.randint(1, 30))
    last_txn = as_of - timedelta(minutes=rng.randint(0, 60))
    txn_count_total = rng.randint(1, 50)

    # 5-minute rolling — owned by ASP acme-feature-window-5m in
    # production ($hoppingWindow 5m / 30s hop). Seeder plants warm-start
    # values so the dashboard tiles + drift detector have something to
    # read on a cold cluster; ASP $merge overwrites them on the next hop
    # close once the customer transacts.
    txn_count_5m = rng.randint(0, 12)
    amount_sum_5m = round(rng.uniform(0.0, 800.0), 2)
    discount_sum_5m = round(rng.uniform(0.0, 80.0), 2)

    # 24h / 7d windows — owned by Spark in production. Seeded with
    # plausible warm-start values; the windows-merge-to-mongo cell will
    # overwrite these with real Delta-derived values once Spark runs.
    txn_count_24h = rng.randint(txn_count_5m, max(txn_count_5m, 80))
    txn_count_7d = rng.randint(txn_count_24h, max(txn_count_24h, 400))
    spend_24h_myr = round(rng.uniform(0.0, 1500.0), 2)
    spend_7d_myr = round(spend_24h_myr * rng.uniform(3.0, 7.0), 2)
    discount_rate_7d = round(rng.uniform(0.0, 0.25), 4)

    # Profile-derived
    pkg = cust.get("package_value_myr")
    if pkg is None:
        pkg = round(rng.uniform(50.0, 300.0), 2)
    spend_to_package_ratio = (
        round(spend_7d_myr / pkg, 4) if pkg > 0 else 0.0
    )

    # Computed signals — would normally land via change-stream from the
    # producer's `computed_signals` block; seeder plants demo values.
    amount_vs_avg_30d_pct = round(rng.uniform(-0.3, 0.6), 4)
    discount_pct_vs_avg_30d = round(rng.uniform(-0.2, 0.5), 4)
    is_high_risk_charge_code = rng.random() < 0.10

    # Overlay onto the canonical doc.
    doc.update({
        "as_of": as_of,
        "first_txn_at": first_txn,
        "last_txn_at": last_txn,
        "feature_set_version": FEATURE_SET_VERSION,
        "txn_count_total": txn_count_total,
        # 5-min rolling
        "txn_count_5m": txn_count_5m,
        "amount_sum_5m": amount_sum_5m,
        "discount_sum_5m": discount_sum_5m,
        "txn_velocity_5m": txn_count_5m,
        # 24h / 7d windows (warm-start)
        "txn_count_24h": txn_count_24h,
        "txn_count_7d": txn_count_7d,
        "spend_24h_myr": spend_24h_myr,
        "spend_7d_myr": spend_7d_myr,
        "discount_rate_7d": discount_rate_7d,
        # Profile-derived
        "package_value_myr": pkg,
        "spend_to_package_ratio": spend_to_package_ratio,
        # Computed signals
        "amount_vs_avg_30d_pct": amount_vs_avg_30d_pct,
        "discount_pct_vs_avg_30d": discount_pct_vs_avg_30d,
        "is_high_risk_charge_code": is_high_risk_charge_code,
    })

    # Lineage: seed earliest/latest from the demo txn window, count from
    # txn_count_total so the seeder mirrors the FE worker's contract.
    doc["lineage"] = {
        "source_transactions_count": txn_count_total,
        "earliest_source_at": first_txn,
        "latest_source_at": last_txn,
    }

    # Quality: low-confidence demo data, origin = seed.
    doc["quality"] = {
        "missing_inputs": [],
        "outlier_flags": [],
        "confidence": round(rng.uniform(0.4, 0.6), 4),
        "computed_via": "seed",
    }

    doc["_seed_marker"] = SEED_MARKER
    return doc


def _validate_canonical(doc: dict) -> None:
    """Defensive check — every canonical field must be present.

    Cheap O(n) over the field tuple, runs once per built doc. Catches
    accidental drift when someone adds a CANONICAL_FEATURE_FIELDS entry
    without updating this seeder.
    """
    missing = [k for k in CANONICAL_FEATURE_FIELDS if k not in doc]
    if missing:
        raise AssertionError(
            f"seed_features built a doc missing canonical fields: {missing}"
        )


async def main(db, *, count: int = 250) -> dict:
    """Seed up to `count` features docs.

    Returns `{inserted, customer_count, marker}`.
    """
    rng = random.Random(23)
    coll = db[FEATURES]

    # Idempotent wipe.
    await _delete_marker(coll, SEED_MARKER)

    customers = await _resolve_customers(db, count, rng)
    now = _utcnow()
    docs: list[dict] = []
    for cust in customers:
        d = _build_feature_doc(cust, rng, now=now)
        _validate_canonical(d)
        docs.append(d)
    await _insert_docs(coll, docs)

    result = {
        "inserted": len(docs),
        "customer_count": len(customers),
        "marker": SEED_MARKER,
    }
    logger.info("seed_features_done", **result)
    return result


async def _entrypoint() -> None:
    configure_logging()
    await connect_mongo()
    try:
        await main(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_entrypoint())
