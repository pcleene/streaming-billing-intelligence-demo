"""PR-2 migration — split legacy `customers` into typed collections.

Reads `customers` (the alias-target), writes each doc to
`customers_residential` or `customers_commercial` based on
`customer_type` (defaults to `residential` for legacy seed data, which
was residential-only). Populates `customer_index` so the route layer
can dispatch by `customer_id` alone.

Idempotent — re-runs are no-ops on already-migrated docs.

Usage:
    python -m scripts.split_customers --dry-run
    python -m scripts.split_customers
    python -m scripts.split_customers --confirm-drop  # drops legacy

Verification:
    python -m scripts.split_customers --verify
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)

# When the legacy alias collection IS the residential collection (which
# is what `CUSTOMERS = CUSTOMERS_RESIDENTIAL` means), there's nothing to
# migrate from `CUSTOMERS` → `CUSTOMERS_RESIDENTIAL` — the data is
# already there. The migration in that case is purely:
#   - tag `customer_type` on each residential doc that lacks it
#   - populate the `customer_index` row
#   - move any commercial docs that landed in the alias by mistake
LEGACY_IS_ALIAS = CUSTOMERS == CUSTOMERS_RESIDENTIAL


def _infer_type(doc: dict) -> str:
    """Infer customer_type from a legacy doc.

    Heuristics:
      - explicit `customer_type` wins.
      - presence of `commercial_profile` or `parent_account_id` ⇒ commercial.
      - otherwise: residential.
    """
    explicit = doc.get("customer_type")
    if explicit in ("residential", "commercial"):
        return explicit
    if doc.get("commercial_profile") or doc.get("parent_account_id"):
        return "commercial"
    return "residential"


def _index_row(doc: dict, ctype: str) -> dict:
    return {
        "customer_id":       doc["customer_id"],
        "customer_type":     ctype,
        "account_id":        doc.get("account_id"),
        "parent_account_id": doc.get("parent_account_id"),
        "name":              doc.get("name"),
    }


async def split(*, dry_run: bool = False) -> dict:
    db = get_db()
    legacy = db[CUSTOMERS]
    res    = db[CUSTOMERS_RESIDENTIAL]
    com    = db[CUSTOMERS_COMMERCIAL]
    idx    = db[CUSTOMER_INDEX]

    moved_residential = 0
    moved_commercial  = 0
    indexed           = 0
    tagged            = 0
    skipped           = 0

    cursor = legacy.find({}, no_cursor_timeout=True)
    try:
        async for doc in cursor:
            cid = doc.get("customer_id")
            if not cid:
                skipped += 1
                continue
            ctype = _infer_type(doc)

            # Stamp the type on the doc itself.
            if doc.get("customer_type") != ctype:
                if not dry_run:
                    await legacy.update_one(
                        {"customer_id": cid},
                        {"$set": {"customer_type": ctype}},
                    )
                tagged += 1

            # Write to the typed collection. If LEGACY_IS_ALIAS and ctype
            # is residential, the doc is already in `customers_residential`
            # (= legacy). Skip the rewrite in that case.
            if not (LEGACY_IS_ALIAS and ctype == "residential"):
                if not dry_run:
                    target = res if ctype == "residential" else com
                    target_doc = dict(doc)
                    target_doc["customer_type"] = ctype
                    await target.replace_one(
                        {"customer_id": cid}, target_doc, upsert=True
                    )
                    if ctype == "commercial" and LEGACY_IS_ALIAS:
                        # Remove the commercial doc from the residential
                        # alias so it doesn't double-count.
                        await legacy.delete_one({"customer_id": cid})
                if ctype == "residential":
                    moved_residential += 1
                else:
                    moved_commercial += 1

            # Populate / refresh the index row.
            if not dry_run:
                await idx.update_one(
                    {"customer_id": cid},
                    {"$set": _index_row(doc, ctype)},
                    upsert=True,
                )
            indexed += 1
    finally:
        await cursor.close()

    summary = {
        "dry_run": dry_run,
        "moved_residential": moved_residential,
        "moved_commercial":  moved_commercial,
        "indexed":           indexed,
        "tagged":            tagged,
        "skipped":           skipped,
    }
    logger.info("split_customers_done", **summary)
    return summary


async def verify() -> dict:
    db = get_db()
    legacy_count = await db[CUSTOMERS].count_documents({})
    res_count    = await db[CUSTOMERS_RESIDENTIAL].count_documents({})
    com_count    = await db[CUSTOMERS_COMMERCIAL].count_documents({})
    idx_count    = await db[CUSTOMER_INDEX].count_documents({})

    # Spot-check: every doc in the typed collections has a matching index row.
    missing_idx = []
    for coll, name in ((db[CUSTOMERS_RESIDENTIAL], CUSTOMERS_RESIDENTIAL),
                       (db[CUSTOMERS_COMMERCIAL], CUSTOMERS_COMMERCIAL)):
        async for doc in coll.find({}, {"customer_id": 1, "_id": 0}).limit(50):
            cid = doc.get("customer_id")
            if cid and not await db[CUSTOMER_INDEX].find_one({"customer_id": cid}):
                missing_idx.append((name, cid))

    summary = {
        "legacy_count":   legacy_count,
        "residential":    res_count,
        "commercial":     com_count,
        "customer_index": idx_count,
        "sample_missing_idx": missing_idx[:10],
    }
    logger.info("split_customers_verify", **summary)
    return summary


async def confirm_drop() -> None:
    """Drop the legacy alias collection ONLY when it isn't the residential
    target (otherwise we'd delete the new typed home).
    """
    db = get_db()
    if LEGACY_IS_ALIAS:
        logger.warning(
            "drop_skipped_legacy_is_alias",
            note="CUSTOMERS aliases CUSTOMERS_RESIDENTIAL — nothing to drop.",
        )
        return
    await db.drop_collection(CUSTOMERS)
    logger.info("legacy_customers_dropped")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Split customers → typed collections")
    p.add_argument("--dry-run", action="store_true", help="Don't write anything")
    p.add_argument("--verify",  action="store_true", help="Print counts and exit")
    p.add_argument("--confirm-drop", action="store_true",
                   help="After migration, drop legacy collection (only if not aliased)")
    return p.parse_args()


async def main() -> None:
    configure_logging()
    args = _parse()
    await connect_mongo()
    try:
        if args.verify:
            await verify()
            return
        await split(dry_run=args.dry_run)
        if args.confirm_drop and not args.dry_run:
            await confirm_drop()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
