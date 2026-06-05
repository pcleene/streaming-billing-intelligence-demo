"""Teardown / wipe Streaming Billing demo data.

Companion to `scripts.seed` — provides separate, idempotent commands so
you can wipe data without re-seeding (and vice-versa).

Three modes:

  python -m scripts.teardown                     # default: empty all
                                                 # collections (delete_many);
                                                 # keep collections, indexes,
                                                 # validators in place. Fast.

  python -m scripts.teardown --full              # drop collections entirely
                                                 # (removes validators +
                                                 # regular indexes); also
                                                 # drops Atlas Search /
                                                 # Vector Search indexes.

  python -m scripts.teardown --only customers    # limit to one or more
                                                 # collections (repeatable).

The default `--data` mode is what you want between demo runs — quick,
preserves the schema. Use `--full` only when you're about to change
schemas / indexes and want a clean slate.

Pairs with:
  python -m scripts.seed                         # regenerate
"""

from __future__ import annotations

import argparse
import asyncio

from pymongo.errors import OperationFailure

from app.core.constants import (
    ALL_COLLECTIONS,
    CUSTOMERS_RESIDENTIAL,
    IDX_CASE_HISTORY_AUTOEMBED,
    IDX_CUSTOMERS_SEARCH,
    QUARANTINE_CASES_HISTORY,
)
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)


# (collection, search_index_name) pairs to drop in `--full` mode.
# AutoEmbed (ADR-032): only the residential autocomplete + the
# AutoEmbed history index remain. Also drop the legacy
# `case_history_vector_idx` if it survived a partial migration.
_SEARCH_INDEX_TARGETS: tuple[tuple[str, str], ...] = (
    (CUSTOMERS_RESIDENTIAL, IDX_CUSTOMERS_SEARCH),
    (QUARANTINE_CASES_HISTORY, IDX_CASE_HISTORY_AUTOEMBED),
    (QUARANTINE_CASES_HISTORY, "case_history_vector_idx"),
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tear down Streaming Billing demo data")
    p.add_argument(
        "--full",
        action="store_true",
        help="Drop collections entirely (also removes validators + indexes) "
             "and drop Atlas Search/Vector Search indexes. Default is "
             "`delete_many` only (fast, schema preserved).",
    )
    p.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="COLLECTION",
        help="Restrict teardown to one or more named collections. Repeatable. "
             "Default targets every streaming_billing collection.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    return p.parse_args()


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


async def _delete_data(db, targets: tuple[str, ...]) -> dict[str, int]:
    """Empty each target collection. Skips collections that don't exist."""
    existing = set(await db.list_collection_names())
    counts: dict[str, int] = {}
    for name in targets:
        if name not in existing:
            logger.info("teardown_skip_missing", collection=name)
            counts[name] = 0
            continue
        result = await db[name].delete_many({})
        counts[name] = result.deleted_count
        logger.info("teardown_data_cleared",
                    collection=name, deleted=result.deleted_count)
    return counts


async def _drop_collections(db, targets: tuple[str, ...]) -> list[str]:
    existing = set(await db.list_collection_names())
    dropped: list[str] = []
    for name in targets:
        if name not in existing:
            logger.info("teardown_skip_missing", collection=name)
            continue
        await db[name].drop()
        dropped.append(name)
        logger.info("teardown_collection_dropped", collection=name)
    return dropped


async def _drop_search_indexes(db, targets: tuple[str, ...]) -> list[str]:
    """Drop Atlas Search / Vector Search indexes for the targeted
    collections. Safe to call when indexes don't exist (the driver
    raises NamespaceNotFound which we swallow).
    """
    existing = set(await db.list_collection_names())
    dropped: list[str] = []
    for coll, index_name in _SEARCH_INDEX_TARGETS:
        if coll not in targets or coll not in existing:
            continue
        try:
            await db[coll].drop_search_index(index_name)
            dropped.append(f"{coll}/{index_name}")
            logger.info("teardown_search_index_dropped",
                        collection=coll, index=index_name)
        except OperationFailure as exc:
            # 27 = IndexNotFound, 26 = NamespaceNotFound — both are no-ops.
            if exc.code in {26, 27}:
                logger.info("teardown_search_index_absent",
                            collection=coll, index=index_name)
            else:
                logger.warning("teardown_search_index_drop_failed",
                               collection=coll, index=index_name,
                               error=str(exc))
    return dropped


async def main() -> None:
    configure_logging()
    args = _parse_args()
    targets: tuple[str, ...] = tuple(args.only) if args.only else ALL_COLLECTIONS

    await connect_mongo()
    try:
        db = get_db()

        if not args.yes:
            mode = "FULL DROP (collections + indexes + validators)" \
                if args.full else "delete documents only"
            print(f"\nAbout to teardown streaming_billing on db={db.name!r}")
            print(f"  Mode    : {mode}")
            print(f"  Targets : {', '.join(targets)}")
            if not _confirm("Proceed?"):
                logger.info("teardown_aborted_by_user")
                return

        logger.info("teardown_started", db=db.name, full=args.full,
                    targets=list(targets))

        if args.full:
            await _drop_search_indexes(db, targets)
            await _drop_collections(db, targets)
        else:
            await _delete_data(db, targets)

        logger.info("teardown_complete")
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
