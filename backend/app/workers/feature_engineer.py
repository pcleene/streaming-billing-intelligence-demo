"""Online feature engineering via change-stream tail.

Tails `transactions` change stream and maintains lineage / counters /
computed-signal pull-through on each customer's features doc. The 5-min
hopping window (`txn_count_5m`, `amount_sum_5m`, `discount_sum_5m`) is
owned by ASP `acme-feature-window-5m` ($hoppingWindow 5m / 30s hop,
$merge into features); this worker no longer maintains a `_recent_window`
buffer. The heavier 24h/7d/30d rollups are written by the Spark
Structured Streaming job (see ml/ notebook).

What this worker still owns:
  * `lineage.source_transactions_count` ($inc), `lineage.earliest_source_at`
    ($min), `lineage.latest_source_at` ($max) per event so the drift
    detector can attribute samples back to the producer window.
  * `txn_count_total` ($inc), `last_txn_at`, `updated_at`.
  * Pull-through of producer-computed signals
    (`amount_vs_avg_30d_pct`, `discount_pct_vs_avg_30d`,
    `is_high_risk_charge_code`) from `txn.computed_signals` onto the
    features doc.
  * `$setOnInsert` of the canonical empty doc when this is the first
    write for a customer.
  * `_sync_customer_embeds` — push to `customers.recent_transactions`
    and snapshot `customers.latest_features` (cross-collection
    read-after-write that ASP can't do cleanly in one pipeline).
  * `_stamp_signals_on_txn` — back-stamp `txn_velocity_5m` and
    `amount_vs_avg_30d_pct` onto the source transaction (slow-path
    enricher; reads features.txn_count_5m which ASP populates).
  * On-demand iforest scoring via `score_customer_if_configured`.

Run via:  python -m app.workers.feature_engineer
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.core.constants import CUSTOMERS, FEATURES, TRANSACTIONS
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.transaction_repo import _build_recent_embed
from app.schemas.feature import canonical_feature_doc
from app.services.quarantine_iforest_scorer import score_customer_if_configured


def _utcnow() -> datetime:
    """Single source of truth for tz-aware UTC 'now' inside this worker."""
    return datetime.now(timezone.utc)


# Subset of FEATURES doc fields mirrored on customers.latest_features (the
# rest come from the offline Spark rollups that maintain 24h/7d/30d windows).
# PR-10 adds the four `computed_signals`-derived fields so downstream rule
# pipelines can read them without re-querying `features`.
_LATEST_FEATURES_FIELDS = (
    "txn_count_1h",
    "txn_count_24h",
    "txn_count_7d",
    "txn_count_5m",
    "spend_24h_myr",
    "spend_7d_myr",
    "discount_rate_30d",
    "quarantine_count_30d",
    "minutes_since_last_txn",
    "minutes_since_last_quarantine",
    "package_value_myr",
    "spend_to_package_ratio",
    "amount_vs_avg_30d_pct",
    "discount_pct_vs_avg_30d",
    "is_high_risk_charge_code",
)

logger = get_logger(__name__)


def _coerce_timestamp(ts: Any) -> datetime:
    """Coerce a txn timestamp to a tz-aware UTC datetime.

    Naive datetimes (e.g. legacy ISO strings without an offset) are assumed
    UTC — matches the simulator's contract.
    """
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            ts = _utcnow()
    if not isinstance(ts, datetime):
        ts = _utcnow()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _derive_feature_writes(txn: dict[str, Any]) -> dict[str, Any]:
    """Pure derivation of a features-collection write spec from a txn.

    Returns a dict with keys: `set`, `inc`, `min`, `max`, `set_on_insert`,
    `use_signals`.

    Behaviour:
      * Always tracks lineage (`lineage.source_transactions_count`,
        `lineage.earliest_source_at`, `lineage.latest_source_at`).
      * Always bumps `txn_count_total` and stamps `last_txn_at` /
        `first_txn_at`.
      * Pulls `amount_vs_avg_30d_pct`, `discount_pct_vs_avg_30d`, and
        `is_high_risk_charge_code` straight from `computed_signals` when
        present — these are V3 extended-reference signals computed at
        insert time by the producer.

    The 5-min hopping window (`txn_count_5m`, `amount_sum_5m`,
    `discount_sum_5m`) is no longer this worker's concern — ASP
    `acme-feature-window-5m` owns those fields.
    """
    cid = txn.get("customer_id")
    ts = _coerce_timestamp(txn.get("timestamp"))

    signals = txn.get("computed_signals") or {}
    use_signals: dict[str, bool] = {}

    set_doc: dict[str, Any] = {
        "customer_id": cid,
        "updated_at": _utcnow(),
        "last_txn_at": ts,
    }

    for sig_field, feature_field in (
        ("amount_vs_avg_30d_pct", "amount_vs_avg_30d_pct"),
        ("discount_pct_vs_avg_30d", "discount_pct_vs_avg_30d"),
    ):
        if sig_field in signals and signals[sig_field] is not None:
            set_doc[feature_field] = signals[sig_field]
            use_signals[feature_field] = True

    if "is_high_risk_charge_code" in signals and signals["is_high_risk_charge_code"] is not None:
        set_doc["is_high_risk_charge_code"] = bool(signals["is_high_risk_charge_code"])
        use_signals["is_high_risk_charge_code"] = True

    inc_doc: dict[str, Any] = {
        "txn_count_total": 1,
        "lineage.source_transactions_count": 1,
    }

    min_doc: dict[str, Any] = {
        "first_txn_at": ts,
        "lineage.earliest_source_at": ts,
    }

    max_doc: dict[str, Any] = {
        "lineage.latest_source_at": ts,
    }

    # Canonical-shape `$setOnInsert`: when this is the FIRST write for a
    # customer, every CANONICAL_FEATURE_FIELDS key lands as a None / 0 /
    # default placeholder, plus a fully-shaped `quality` sub-doc. We must
    # strip any key that conflicts with `$set` / `$inc` / `$min` / `$max`
    # because Mongo raises ConflictingUpdateOperators when the same path
    # is targeted by two operators in the same update.
    set_on_insert = canonical_feature_doc(cid, now=_utcnow())
    # `$inc`/`$min`/`$max` all target dotted children of `lineage` —
    # touching the parent in `$setOnInsert` is a conflict, so drop it.
    set_on_insert.pop("lineage", None)
    # Drop any top-level key actively `$set` by this update.
    for k in set_doc:
        set_on_insert.pop(k, None)
    # `first_txn_at` is `$min`'d.
    set_on_insert.pop("first_txn_at", None)

    return {
        "set": set_doc,
        "inc": inc_doc,
        "min": min_doc,
        "max": max_doc,
        "set_on_insert": set_on_insert,
        "use_signals": use_signals,
    }


class FeatureEngineer:
    def __init__(self) -> None:
        self._stop = asyncio.Event()

    async def run(self) -> None:
        db = get_db()
        coll = db[TRANSACTIONS]
        feat = db[FEATURES]
        customers = db[CUSTOMERS]
        pipeline = [{"$match": {"operationType": "insert"}}]
        # pymongo-async returns a coroutine for `watch(...)` that must be
        # awaited before entering the async-context-manager — the older
        # synchronous form (`async with coll.watch(...) as stream:`) raises
        # `TypeError: 'coroutine' object does not support the async ctx mgr
        # protocol` on current driver versions.
        async with await coll.watch(pipeline=pipeline, full_document="updateLookup") as stream:
            logger.info("feature_engineer_change_stream_open")
            while not self._stop.is_set():
                # `try_next()` is the non-blocking poll; pymongo-async's
                # blocking `next()` cannot be cancelled via `asyncio.wait_for`
                # without killing the underlying cursor (the next call then
                # raises StopAsyncIteration). Polling at 250 ms keeps the
                # stop-signal latency low without burning CPU.
                try:
                    change = await stream.try_next()
                except StopAsyncIteration:
                    break
                if change is None:
                    await asyncio.sleep(0.25)
                    continue
                doc = change.get("fullDocument") or {}
                await self._update_features(feat, doc)
                await self._sync_customer_embeds(customers, feat, doc)
                # PR-15 slow-path: stamp txn_velocity_5m + amount_vs_avg_30d_pct
                # back onto the source txn now that the features doc is fresh.
                await self._stamp_signals_on_txn(coll, feat, doc)
                cid = doc.get("customer_id")
                if cid:
                    await score_customer_if_configured(db, cid, settings)

    async def _update_features(self, feat, txn: dict[str, Any]) -> None:
        cid = txn.get("customer_id")
        if not cid:
            return

        spec = _derive_feature_writes(txn)

        # Single upsert: lineage / counters / last_txn_at / pulled-through
        # signals + canonical-shape $setOnInsert. The 5-min hopping window
        # fields are written by ASP acme-feature-window-5m and are NOT
        # touched here.
        await feat.update_one(
            {"customer_id": cid},
            {
                "$set": spec["set"],
                "$inc": spec["inc"],
                "$min": spec["min"],
                "$max": spec["max"],
                "$setOnInsert": spec["set_on_insert"],
            },
            upsert=True,
        )

        if spec["use_signals"]:
            logger.debug(
                "feature_engineer_used_signals",
                customer_id=cid,
                fields=list(spec["use_signals"].keys()),
            )

    async def _sync_customer_embeds(self, customers, feat, txn: dict[str, Any]) -> None:
        """Mirror onto customers: push recent_transactions and snapshot
        latest_features. Both are silent no-ops if the customer is missing.
        """
        cid = txn.get("customer_id")
        if not cid:
            return
        try:
            embed = _build_recent_embed(txn)
            await customers.update_one(
                {"customer_id": cid},
                {"$push": {
                    "recent_transactions": {
                        "$each": [embed],
                        "$position": 0,
                        "$slice": 50,
                    }
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("recent_txn_embed_failed", customer_id=cid, error=str(exc))

        try:
            features_doc = await feat.find_one(
                {"customer_id": cid},
                {"_id": 0, **{f: 1 for f in _LATEST_FEATURES_FIELDS}, "updated_at": 1},
            )
            if not features_doc:
                return
            snapshot: dict[str, Any] = {
                "as_of": features_doc.get("updated_at") or _utcnow(),
            }
            for field in _LATEST_FEATURES_FIELDS:
                if field in features_doc:
                    snapshot[field] = features_doc[field]
            await customers.update_one(
                {"customer_id": cid},
                {"$set": {"latest_features": snapshot}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("latest_features_embed_failed", customer_id=cid, error=str(exc))

    async def _stamp_signals_on_txn(self, txns, feat, txn: dict[str, Any]) -> None:
        """Stamp the two rolling `computed_signals` back onto the source txn.

        Idempotent: skips when the producer already wrote both fields
        (seeded txns carry them from `seed_transactions._txns_for_customer`).
        Touches at most one features read + one txns update per call. Any
        partial failure is logged and swallowed — this is a slow-path
        enricher, not the ingest hot-path, and a missed stamp just means
        the dashboard tile shows null until the next run picks it up via
        a future change-stream event on the same customer.
        """
        cid = txn.get("customer_id")
        txn_id = txn.get("transaction_id")
        if not cid or not txn_id:
            return

        existing = txn.get("computed_signals") or {}
        needs_velocity = existing.get("txn_velocity_5m") is None
        needs_avg = existing.get("amount_vs_avg_30d_pct") is None
        if not (needs_velocity or needs_avg):
            return

        set_doc: dict[str, Any] = {}

        try:
            if needs_velocity:
                feats = await feat.find_one(
                    {"customer_id": cid},
                    {"_id": 0, "txn_count_5m": 1},
                )
                if feats and feats.get("txn_count_5m") is not None:
                    set_doc["computed_signals.txn_velocity_5m"] = int(feats["txn_count_5m"])

            if needs_avg:
                ts = _coerce_timestamp(txn.get("timestamp"))
                cutoff = ts - timedelta(days=30)
                # Aggregate over the prior 30 days for this customer.
                # Indexed by (customer_id, timestamp) — cheap at demo scale.
                # pymongo-async returns a coroutine for `aggregate(...)` —
                # must `await` it to get the cursor.
                cursor = await txns.aggregate([
                    {"$match": {
                        "customer_id": cid,
                        "timestamp": {"$gte": cutoff, "$lt": ts},
                    }},
                    {"$group": {
                        "_id": None,
                        "avg": {"$avg": "$total_myr"},
                        "n": {"$sum": 1},
                    }},
                ])
                rows = await cursor.to_list(length=1)
                row = rows[0] if rows else None
                avg = row.get("avg") if row else None
                n = row.get("n") if row else 0
                cur_amount = float(txn.get("total_myr") or 0.0)
                # Need at least 3 prior samples for the ratio to mean anything;
                # under that we leave the field null and let the next event
                # try again.
                if avg and avg > 0 and n >= 3:
                    ratio = (cur_amount - avg) / avg
                    set_doc["computed_signals.amount_vs_avg_30d_pct"] = round(ratio, 4)

            if set_doc:
                set_doc["computed_signals.computed_at"] = _utcnow()
                await txns.update_one(
                    {"transaction_id": txn_id},
                    {"$set": set_doc},
                )
                logger.debug(
                    "txn_signals_stamped",
                    transaction_id=txn_id,
                    customer_id=cid,
                    fields=[k.split(".", 1)[1] for k in set_doc if k.startswith("computed_signals.") and not k.endswith("computed_at")],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "txn_signals_stamp_failed",
                transaction_id=txn_id,
                customer_id=cid,
                error=str(exc),
            )

    async def shutdown(self) -> None:
        self._stop.set()


async def main() -> None:
    configure_logging()
    await connect_mongo()
    from app.services.quarantine_iforest_scorer import init_scorer

    init_scorer()
    fe = FeatureEngineer()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(fe.shutdown()))
    try:
        await fe.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
