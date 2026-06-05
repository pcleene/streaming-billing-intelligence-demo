"""Tail charge_codes change stream → hot-update the in-process cache.

Pairs with `app.services.charge_code_cache.charge_code_cache`. Every
uvicorn worker / background process that needs the catalog runs one of
these alongside; the watcher sees insert / update / delete /
replace events and patches its cache instance accordingly. Within ~2s
of an admin edit through `ChargeCodeRepository.upsert`, every running
process reflects the change.

The change-handling logic lives in `apply_change(change, *, cache)` so
unit tests can drive it directly without a live Mongo. The async
`_Watcher` wraps that with the standard async-loop boilerplate (mirrors
`app.workers.rule_change_watcher`).

Run via:  python -m app.workers.charge_code_change_watcher
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from app.core.constants import CHARGE_CODES
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.charge_code_repo import ChargeCodeRepository
from app.services.charge_code_cache import ChargeCodeCache, charge_code_cache

logger = get_logger(__name__)


def apply_change(change: dict[str, Any], *, cache: ChargeCodeCache) -> str:
    """Translate one change-stream event into a cache mutation.

    Returns the action taken (`"upsert"`, `"remove"`, `"noop"`) so the
    caller / test can assert on outcomes.
    """
    op = change.get("operationType")
    doc = change.get("fullDocument") or {}
    if op in {"insert", "update", "replace"}:
        if not doc.get("code"):
            logger.warning("charge_code_change_missing_code", op=op)
            return "noop"
        cache.upsert(doc)
        return "upsert"
    if op == "delete":
        # Pre-image is not always available; fall back to documentKey.
        code = (
            (change.get("fullDocumentBeforeChange") or {}).get("code")
            or (change.get("documentKey") or {}).get("code")
        )
        if not code:
            logger.warning("charge_code_change_delete_missing_code")
            return "noop"
        cache.remove(code)
        return "remove"
    return "noop"


class _Watcher:
    def __init__(self) -> None:
        self._stop = asyncio.Event()

    async def run(self) -> None:
        db = get_db()
        # Cold-load before tailing the stream so the cache is hot from
        # tick zero. New events will overlay; lost-update is impossible
        # because the repo is the source of truth.
        await charge_code_cache.load(ChargeCodeRepository(db))
        async with db[CHARGE_CODES].watch(full_document="updateLookup") as stream:
            logger.info("charge_code_change_stream_open")
            while not self._stop.is_set():
                try:
                    change = await asyncio.wait_for(stream.next(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                action = apply_change(change, cache=charge_code_cache)
                logger.info(
                    "charge_code_change_applied",
                    action=action,
                    op=change.get("operationType"),
                    code=(change.get("fullDocument") or {}).get("code"),
                )

    async def shutdown(self) -> None:
        self._stop.set()


async def main() -> None:
    configure_logging()
    await connect_mongo()
    w = _Watcher()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(w.shutdown()))
    try:
        await w.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
