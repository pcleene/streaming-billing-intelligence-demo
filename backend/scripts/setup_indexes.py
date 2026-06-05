"""Create operational indexes + Atlas Search/Vector Search indexes.

PR-1 rewrite — declares every index per `2026-05-04-schema-richness-design.md`
§9 plus the legacy demo indexes. Idempotent: re-running just no-ops on
existing indexes / search-indexes.

Atlas Search/Vector Search indexes require the `searchIndexes` command
(driver-side); we issue them and then poll briefly until they go QUERYABLE.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pymongo import ASCENDING, DESCENDING, GEOSPHERE

from app.config import settings
from app.core.constants import (
    BILL_CYCLES,
    CHARGE_CODES,
    CRM_SNAPSHOTS,
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    EMBED_SOURCE_PATH,
    FEATURE_DRIFT_METRICS,
    FEATURES,
    IDX_CASE_HISTORY_AUTOEMBED,
    IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
    IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
    IDX_CUSTOMERS_SEARCH,
    QUARANTINE_CASES,
    QUARANTINE_CASES_HISTORY,
    QUARANTINE_RULES,
    SYSTEM_METRICS,
    TRANSACTIONS,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# --- Regular indexes ----------------------------------------------------
# Per `2026-05-04-schema-richness-design.md` §9. Legacy `CUSTOMERS`
# (== `CUSTOMERS_RESIDENTIAL` alias) keeps its original lean indexes so
# existing seed data continues to be queryable during the PR-2 transition.

_LEGACY_CUSTOMERS_INDEXES = [
    ([("customer_id", ASCENDING)],   {"unique": True, "name": "customer_id_unique"}),
    ([("contact.email", ASCENDING)], {"sparse": True, "name": "email_sparse"}),
    ([("tier", ASCENDING)],          {"name": "tier"}),
]

REGULAR_INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict[str, Any]]]] = {
    # CUSTOMERS is a transitional alias for CUSTOMERS_RESIDENTIAL — declare
    # both keys so the dict stays explicit if the alias ever moves.
    CUSTOMERS_RESIDENTIAL: [
        ([("customer_id", ASCENDING)], {"unique": True, "name": "customer_id_unique"}),
        ([("account_id", ASCENDING)],  {"unique": True, "sparse": True, "name": "account_id_unique"}),
        ([("contact.email", ASCENDING)],
         {"sparse": True, "name": "email_sparse"}),
        ([("address.location", GEOSPHERE)],
         {"name": "address_location_2dsphere",
          "partialFilterExpression": {
              "address.location": {"$exists": True}
          }}),
        ([("tier", ASCENDING)], {"name": "tier"}),
        # Phase A embed support — accelerates "open cases on this customer"
        ([("open_cases.case_id", ASCENDING)],
         {"sparse": True, "name": "open_cases_case_id"}),
    ],
    CUSTOMERS_COMMERCIAL: [
        ([("customer_id", ASCENDING)], {"unique": True, "name": "customer_id_unique"}),
        ([("account_id", ASCENDING)],  {"unique": True, "sparse": True, "name": "account_id_unique"}),
        ([("parent_account_id", ASCENDING)],
         {"sparse": True, "name": "parent_account_id_sparse"}),
        ([("business_profile.business_registration_no", ASCENDING)],
         {"sparse": True, "name": "business_registration_no"}),
        ([("business_profile.registered_address.location", GEOSPHERE)],
         {"name": "registered_address_2dsphere",
          "partialFilterExpression": {
              "business_profile.registered_address.location": {"$exists": True}
          }}),
    ],
    CUSTOMER_INDEX: [
        ([("customer_id", ASCENDING)], {"unique": True, "name": "customer_id_unique"}),
        ([("parent_account_id", ASCENDING)],
         {"sparse": True, "name": "parent_account_id_sparse"}),
        ([("customer_type", ASCENDING)], {"name": "customer_type"}),
    ],
    TRANSACTIONS: [
        ([("transaction_id", ASCENDING)], {"unique": True, "name": "transaction_id_unique"}),
        ([("customer_id", ASCENDING), ("timestamp", DESCENDING)], {"name": "customer_recent"}),
        ([("cycle_id", ASCENDING), ("timestamp", DESCENDING)],
         {"sparse": True, "name": "cycle_recent"}),
        ([("timestamp", DESCENDING)], {"name": "ts_desc"}),
        ([("merchant_id", ASCENDING), ("timestamp", DESCENDING)], {"name": "merchant_recent"}),
        # Geographic-anomaly + analytics
        ([("customer_type", ASCENDING),
          ("location.state", ASCENDING),
          ("timestamp", DESCENDING)],
         {"sparse": True, "name": "type_state_recent"}),
        # Category roll-ups (PR-3 items[].product_ref)
        ([("items.product_ref", ASCENDING)],
         {"sparse": True, "name": "items_product_ref"}),
        # Queue tile — only quarantined=true rows
        ([("quarantined", ASCENDING)],
         {"name": "quarantined_only",
          "partialFilterExpression": {"quarantined": True}}),
        # Campaign attribution lookups
        ([("campaign_attribution.campaign_id", ASCENDING)],
         {"sparse": True, "name": "campaign_attribution_campaign_id"}),
    ],
    BILL_CYCLES: [
        ([("cycle_id", ASCENDING)], {"unique": True, "name": "cycle_id_unique"}),
        ([("customer_id", ASCENDING), ("cycle_start", DESCENDING)],
         {"name": "customer_cycle_recent"}),
        ([("status", ASCENDING)], {"name": "status"}),
    ],
    CHARGE_CODES: [
        ([("code", ASCENDING)], {"unique": True, "name": "code_unique"}),
        ([("revenue_category", ASCENDING)], {"sparse": True, "name": "revenue_category"}),
    ],
    QUARANTINE_RULES: [
        ([("rule_id", ASCENDING)], {"unique": True, "name": "rule_id_unique"}),
        ([("name", ASCENDING)], {"unique": True, "name": "rule_name_unique"}),
        ([("rule_type", ASCENDING), ("mode", ASCENDING)], {"name": "type_mode"}),
    ],
    QUARANTINE_CASES: [
        ([("case_id", ASCENDING)], {"unique": True, "name": "case_id_unique"}),
        ([("customer_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
         {"name": "customer_status_recent"}),
        ([("status", ASCENDING), ("severity", ASCENDING), ("created_at", DESCENDING)],
         {"name": "queue_index"}),
        ([("cycle_id", ASCENDING)], {"sparse": True, "name": "cycle_id"}),
        ([("priority_band", ASCENDING), ("priority_score", DESCENDING)],
         {"sparse": True, "name": "priority"}),
        ([("sla.target_resolution_at", ASCENDING)],
         {"sparse": True, "name": "sla_target"}),
    ],
    QUARANTINE_CASES_HISTORY: [
        ([("case_id", ASCENDING)], {"unique": True, "name": "case_id_unique"}),
        ([("resolved_at", DESCENDING)], {"name": "resolved_recent"}),
        ([("rules_triggered.rule_type", ASCENDING)], {"name": "rule_type"}),
        ([("disposition", ASCENDING)], {"name": "disposition"}),
    ],
    FEATURES: [
        ([("customer_id", ASCENDING)], {"unique": True, "name": "customer_id_unique"}),
        ([("updated_at", DESCENDING)], {"name": "freshness"}),
        ([("customer_id", ASCENDING), ("as_of", DESCENDING)],
         {"sparse": True, "name": "customer_as_of"}),
    ],
    CRM_SNAPSHOTS: [
        ([("customer_id", ASCENDING), ("snapshot_at", DESCENDING)],
         {"name": "customer_snapshot_recent"}),
        ([("snapshot_at", DESCENDING)], {"name": "snapshot_recent"}),
    ],
    SYSTEM_METRICS: [
        # TTL: drop samples older than `system_metrics_ttl_days`.
        ([("recorded_at", ASCENDING)],
         {"name": "recorded_at_ttl",
          "expireAfterSeconds": settings.system_metrics_ttl_days * 24 * 3600}),
        ([("burst_run_id", ASCENDING), ("recorded_at", DESCENDING)],
         {"name": "by_burst_run"}),
    ],
    FEATURE_DRIFT_METRICS: [
        ([("feature_name", ASCENDING), ("measured_at", DESCENDING)],
         {"name": "by_feature_recent"}),
        ([("severity", ASCENDING), ("measured_at", DESCENDING)],
         {"name": "by_severity_recent"}),
        # 30-day TTL — drift series doesn't need to live longer than its
        # baseline.
        ([("measured_at", ASCENDING)],
         {"name": "measured_at_ttl",
          "expireAfterSeconds": 30 * 24 * 3600}),
    ],
}

# If `CUSTOMERS` resolves to something other than the residential
# collection (it shouldn't, but guard anyway), declare its legacy indexes
# under the alias too so legacy data remains queryable.
if CUSTOMERS != CUSTOMERS_RESIDENTIAL:  # pragma: no cover - alias guard
    REGULAR_INDEXES[CUSTOMERS] = _LEGACY_CUSTOMERS_INDEXES


# --- Atlas Search definitions ------------------------------------------
# PR-15 flat-root: `unified_profile` retired — autocomplete paths are now
# directly off the document root (name, contact.email, address.*).
SEARCH_DEFS: list[tuple[str, str, dict]] = [
    (
        CUSTOMERS_RESIDENTIAL,
        IDX_CUSTOMERS_SEARCH,
        {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "customer_id": [{"type": "token"}, {"type": "autocomplete"}],
                    "name":        [{"type": "string"}, {"type": "autocomplete"}],
                    "tier":        {"type": "token"},
                    "contact": {
                        "type": "document",
                        "fields": {
                            "email": [{"type": "string"}, {"type": "autocomplete"}],
                            "phone": {"type": "string"},
                        },
                    },
                    "address": {
                        "type": "document",
                        "fields": {
                            "state": {"type": "token"},
                            "city":  [{"type": "string"}, {"type": "autocomplete"}],
                        },
                    },
                },
            }
        },
    ),
]


# --- Atlas Auto Embedding definitions ----------------------------------
# AutoEmbed (ADR-032): vector search runs only against
# `quarantine_cases_history`. The index points at the `embed_source.text`
# leaf string at depth 1 — Atlas reads the text, calls the configured
# Voyage credential (project-level, not in app code), and stores the
# vector invisibly. Filter fields stay identical to the legacy
# `case_history_vector_idx` so query shapes do not change. See
# `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for the
# pre-AutoEmbed BYO definition we are replacing.
_HISTORY_AUTOEMBED_DEF = {
    "fields": [
        {
            # AutoEmbed (ADR-032): the field type is `autoEmbed`, NOT
            # `vector`. Atlas owns the vector — `numDimensions`,
            # `similarity`, and `input_type` are *not* declared on the
            # field; Atlas derives them from the configured `model`
            # and the project-level Voyage credential. The required
            # keys on this cluster are:
            #   - type:    "autoEmbed"
            #   - path:    leaf string path on the document
            #   - modality:"text"
            #   - model:   the Voyage model name
            # Adding `numDimensions`, `similarity`, or `input_type`
            # makes Atlas reject the field with `unrecognized field`.
            "type": "autoEmbed",
            "path": EMBED_SOURCE_PATH,
            "modality": "text",
            "model": "voyage-4-large",
        },
        {"type": "filter", "path": "rules_triggered.rule_type"},
        {"type": "filter", "path": "disposition"},
        {"type": "filter", "path": "customer_tier"},
        {"type": "filter", "path": "severity"},
    ]
}

# AutoEmbed index for residential customers — powers
# `GET /api/customers/search` semantic queries. Filter paths mirror the
# search-page facets (tier, entities, state, customer_type) so the
# `$vectorSearch.filter` push-down stays inside the index. We project
# `customer_type` as well even though the index lives on the residential
# collection: the same query path also runs against the commercial
# index, and keeping the filter declaration symmetric across the two
# indexes simplifies the service-layer wiring.
_CUSTOMERS_AUTOEMBED_FIELDS = [
    {
        "type": "autoEmbed",
        "path": EMBED_SOURCE_PATH,
        "modality": "text",
        "model": "voyage-4-large",
    },
    {"type": "filter", "path": "tier"},
    {"type": "filter", "path": "customer_type"},
    {"type": "filter", "path": "entities"},
    {"type": "filter", "path": "address.state"},
]

_CUSTOMERS_RESIDENTIAL_AUTOEMBED_DEF = {"fields": _CUSTOMERS_AUTOEMBED_FIELDS}
_CUSTOMERS_COMMERCIAL_AUTOEMBED_DEF = {"fields": _CUSTOMERS_AUTOEMBED_FIELDS}

VECTOR_DEFS: list[tuple[str, str, dict]] = [
    (QUARANTINE_CASES_HISTORY, IDX_CASE_HISTORY_AUTOEMBED, _HISTORY_AUTOEMBED_DEF),
    (
        CUSTOMERS_RESIDENTIAL,
        IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
        _CUSTOMERS_RESIDENTIAL_AUTOEMBED_DEF,
    ),
    (
        CUSTOMERS_COMMERCIAL,
        IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
        _CUSTOMERS_COMMERCIAL_AUTOEMBED_DEF,
    ),
]


# --- AutoEmbed READY-poll knobs ----------------------------------------
# Atlas creates the index asynchronously; queries against a non-READY
# index return zero results. We poll up to 10 minutes (`createSearchIndex`
# typical SLA on M0/M10 is ~2 minutes) before giving up — long enough for
# fresh seeds, short enough that operators see a clear failure if Atlas
# is degraded.
_AUTOEMBED_READY_TIMEOUT_S: float = 600.0
_AUTOEMBED_POLL_INTERVAL_S: float = 2.0


async def create_regular_indexes(db) -> None:
    for coll, defs in REGULAR_INDEXES.items():
        for keys, opts in defs:
            await db[coll].create_index(keys, **opts)
        logger.info("regular_indexes_done", collection=coll, count=len(defs))


async def _list_search_index_names(db, coll: str) -> set[str]:
    try:
        cursor = await db[coll].aggregate([{"$listSearchIndexes": {}}])
        rows = [r async for r in cursor]
        return {r.get("name") for r in rows}
    except Exception:  # collection has no search indexes yet
        return set()


async def create_search_indexes(db) -> None:
    for coll, name, definition in SEARCH_DEFS:
        existing = await _list_search_index_names(db, coll)
        if name in existing:
            logger.info("search_index_exists", collection=coll, name=name)
            continue
        try:
            await db.command({
                "createSearchIndexes": coll,
                "indexes": [{"name": name, "definition": definition}],
            })
            logger.info("search_index_created", collection=coll, name=name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_index_failed", collection=coll, name=name, error=str(exc))


async def _drop_legacy_vector_index(db, coll: str, legacy_name: str) -> None:
    """Drop the pre-AutoEmbed BYO-Voyage vector index if it still exists.

    Idempotent. The legacy `case_history_vector_idx` and the new
    `case_history_autoembed_idx` cannot coexist on the same path because
    the legacy index pointed at the now-retired `embedding` field —
    keeping it around would just waste cluster resources.
    """
    existing = await _list_search_index_names(db, coll)
    if legacy_name not in existing:
        return
    try:
        await db.command({"dropSearchIndex": coll, "name": legacy_name})
        logger.info("legacy_vector_index_dropped", collection=coll, name=legacy_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "legacy_vector_index_drop_failed",
            collection=coll, name=legacy_name, error=str(exc),
        )


async def _autoembed_status(db, coll: str, name: str) -> tuple[str | None, bool]:
    """Return `(status, queryable)` for a single search index, or
    `(None, False)` if the index is missing.

    Atlas exposes `status` (one of READY / FAILED / PENDING / BUILDING)
    plus a boolean `queryable`. The poll uses both — `queryable=True` is
    the only safe state for `$vectorSearch`.
    """
    try:
        cursor = await db[coll].aggregate([{"$listSearchIndexes": {}}])
        async for row in cursor:
            if row.get("name") == name:
                return row.get("status"), bool(row.get("queryable", False))
    except Exception:  # noqa: BLE001
        pass
    return None, False


async def _await_autoembed_ready(
    db, coll: str, name: str,
    *,
    timeout_s: float = _AUTOEMBED_READY_TIMEOUT_S,
    interval_s: float = _AUTOEMBED_POLL_INTERVAL_S,
) -> bool:
    """Poll `$listSearchIndexes` until the index is queryable.

    Returns True on READY+queryable, False on timeout or terminal
    failure. Logs every state transition so operators can see how long
    AutoEmbed indexing actually takes against their tier.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_status: str | None = None
    while asyncio.get_event_loop().time() < deadline:
        status, queryable = await _autoembed_status(db, coll, name)
        if status != last_status:
            logger.info(
                "autoembed_index_status",
                collection=coll, name=name,
                status=status, queryable=queryable,
            )
            last_status = status
        if queryable and status in ("READY", "STEADY"):
            return True
        if status == "FAILED":
            logger.warning(
                "autoembed_index_failed",
                collection=coll, name=name,
            )
            return False
        await asyncio.sleep(interval_s)
    logger.warning(
        "autoembed_index_ready_timeout",
        collection=coll, name=name, timeout_s=timeout_s,
    )
    return False


async def create_vector_indexes(db) -> None:
    """Create the AutoEmbed vector index (ADR-032).

    Drops the legacy BYO-Voyage `case_history_vector_idx` first so the
    two definitions cannot coexist, then issues the AutoEmbed index via
    `createSearchIndexes` with `type=vectorSearch`. Polling for READY
    happens in `setup_all_indexes` so operators can run the index
    creation without waiting.
    """
    # Tear down the legacy BYO-Voyage index. Safe to call repeatedly —
    # `_drop_legacy_vector_index` is idempotent.
    await _drop_legacy_vector_index(
        db, QUARANTINE_CASES_HISTORY, "case_history_vector_idx",
    )

    for coll, name, definition in VECTOR_DEFS:
        existing = await _list_search_index_names(db, coll)
        if name in existing:
            logger.info("vector_index_exists", collection=coll, name=name)
            continue
        try:
            await db.command({
                "createSearchIndexes": coll,
                "indexes": [{"name": name, "definition": definition, "type": "vectorSearch"}],
            })
            logger.info("autoembed_index_created", collection=coll, name=name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vector_index_failed", collection=coll, name=name, error=str(exc))


async def setup_all_indexes(db) -> None:
    await create_regular_indexes(db)
    await create_search_indexes(db)
    await create_vector_indexes(db)
    # AutoEmbed indexes build asynchronously on Atlas. Poll until queryable
    # so callers (seed scripts, integration tests) don't fire `$vectorSearch`
    # against a still-building index.
    for coll, name, _definition in VECTOR_DEFS:
        await _await_autoembed_ready(db, coll, name)


async def _main() -> None:
    """Standalone entry: `python -m scripts.setup_indexes`."""
    from app.core.logging import configure_logging
    from app.deps import connect_mongo, disconnect_mongo, get_db

    configure_logging()
    await connect_mongo()
    try:
        await setup_all_indexes(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_main())
