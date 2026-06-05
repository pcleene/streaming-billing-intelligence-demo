"""Tail quarantine_rules change stream → broadcast SSE rule-change events.

When an analyst toggles a rule via the Rule Studio, ASP processors don't
hot-reload — the operator runs `make asp-deploy` to re-materialise. This
worker just notifies subscribed dashboards so they can show "rule X is
now active" in real time.

Also tails quarantine_cases inserts to broadcast new_case events for the
analyst dashboard.

Run via:  python -m app.workers.rule_change_watcher
"""

from __future__ import annotations

import asyncio
import signal

from app.core.constants import QUARANTINE_CASES, QUARANTINE_RULES
from app.core.logging import configure_logging, get_logger
from app.core.metrics import quarantine_window
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.quarantine_case_repo import (
    EMBED_OPEN_STATUSES,
    QuarantineCaseRepository,
)
from app.routes.stream import (
    publish_case_update,
    publish_new_case,
    publish_rule_changed,
)

logger = get_logger(__name__)


class _Watcher:
    def __init__(self) -> None:
        self._stop = asyncio.Event()

    async def run(self) -> None:
        db = get_db()
        await asyncio.gather(
            self._watch_rules(db),
            self._watch_cases(db),
        )

    async def _watch_rules(self, db) -> None:
        async with db[QUARANTINE_RULES].watch(full_document="updateLookup") as stream:
            logger.info("rule_change_stream_open")
            while not self._stop.is_set():
                try:
                    change = await asyncio.wait_for(stream.next(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                doc = change.get("fullDocument") or {}
                if doc:
                    publish_rule_changed({
                        "rule_id": doc.get("rule_id"),
                        "name": doc.get("name"),
                        "rule_type": doc.get("rule_type"),
                        "mode": doc.get("mode"),
                        "enabled": doc.get("enabled"),
                        "operation": change.get("operationType"),
                    })

    async def _watch_cases(self, db) -> None:
        case_repo = QuarantineCaseRepository(db)
        async with db[QUARANTINE_CASES].watch(full_document="updateLookup") as stream:
            logger.info("case_change_stream_open")
            while not self._stop.is_set():
                try:
                    change = await asyncio.wait_for(stream.next(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                doc = change.get("fullDocument") or {}
                op = change.get("operationType")
                if not doc:
                    continue
                stripped = {k: v for k, v in doc.items() if k != "_id"}
                if op == "insert":
                    quarantine_window.record()
                    publish_new_case(stripped)
                    # Mirror on customers.open_cases for the 360 view.
                    try:
                        await case_repo.sync_open_case_to_customer(stripped)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "open_case_embed_sync_failed",
                            case_id=stripped.get("case_id"),
                            error=str(exc),
                        )
                else:
                    publish_case_update(stripped)
                    # Keep the embed in sync with status transitions. Resolved /
                    # dismissed cases are pulled; open ↔ under_review flips
                    # update the embed in place.
                    try:
                        cid = stripped.get("customer_id")
                        cs = stripped.get("status")
                        if not cid or not cs:
                            continue
                        if cs in EMBED_OPEN_STATUSES:
                            await case_repo.update_open_case_status_on_customer(
                                customer_id=cid,
                                case_id=stripped["case_id"],
                                status=cs,
                            )
                        else:
                            await case_repo.remove_open_case_from_customer(
                                customer_id=cid,
                                case_id=stripped["case_id"],
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "open_case_embed_update_failed",
                            case_id=stripped.get("case_id"),
                            error=str(exc),
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
