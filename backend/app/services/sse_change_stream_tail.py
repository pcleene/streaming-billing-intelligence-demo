"""In-process Mongo change-stream tails that drive SSE broadcasts.

The producers/consumers that mutate `transactions` and `quarantine_cases`
run as separate processes (workers locally, or on the EC2 bastion when
MSK is the source). `app.routes.stream._subscribers` is in-process only,
so those workers cannot push live updates to dashboard SSE clients
attached to the FastAPI backend.

To close that gap without changing transport, the backend's own lifespan
tails the two collections and republishes a compact projection through
the local `publish_*` helpers. The dashboard then sees updates regardless
of where the writes originated (ASP, EC2 consumer, local simulator).

Lifecycle:
  - `start(db)` spawns one asyncio task per stream and returns a handle.
  - `stop(handle)` cancels them and awaits clean exit; safe to call even
    if the streams never opened (e.g. transient connectivity at boot).

Failure semantics:
  - A change-stream error is logged and the loop retries after a short
    backoff. The API process must not die because Mongo blipped.
  - A publish failure for one event is swallowed; SSE is best-effort.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.core.constants import QUARANTINE_CASES, TRANSACTIONS
from app.core.logging import get_logger
from app.core.metrics import (
    quarantine_window,
    rule_eval_window,
    transactions_window,
)
from app.routes.stream import (
    publish_case_update,
    publish_new_case,
    publish_new_transaction,
)

logger = get_logger(__name__)


# Backoff between retries when the change stream errors out. Kept short so
# transient blips heal quickly; capped so we don't spin if Atlas is down.
_RETRY_BACKOFF_S = 2.0

# Cap on the ingest-to-API lag we'll feed into rule_eval_window. The
# change-stream tail is the only thing populating eval p50/p99 when writes
# originate outside the API process (ASP via MSK). End-to-end the path is
# simulator → MSK → ASP → Atlas $merge → change stream → API, which in
# steady state hovers around 1-10 s. The cap is matched to the rolling
# window (60 s) so a stale boot replay can't poison percentiles for more
# than one window.
_EVAL_LAG_CAP_MS = 60_000.0


@dataclass
class TailHandle:
    """Opaque handle returned by `start`; pass back to `stop`."""

    tasks: list[asyncio.Task] = field(default_factory=list)
    stop: asyncio.Event = field(default_factory=asyncio.Event)


def _iso(value: Any) -> Any:
    """Coerce a datetime to ISO-8601; pass everything else through."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_aware_datetime(value: Any) -> datetime | None:
    """Parse a Mongo timestamp field that may be a BSON Date (datetime) or
    a stringified ISO-8601. Returns a tz-aware UTC datetime or ``None``."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _project_txn(doc: dict[str, Any]) -> dict[str, Any]:
    """Trim a persisted V3 transaction doc to the dashboard's live feed shape.

    The producer-side event uses ``amount`` / ``charge_code`` / ``status`` /
    ``merchant``, but the persisted V3 doc (via ``insert_extref``) renames
    those to ``total_myr`` / ``items[*].charge_code`` / ``quarantined`` /
    ``merchant_id``. The frontend `LiveTxn` contract is the producer shape,
    so we map back here. Without this, the dashboard table renders dashes
    for every column except ``customer_id``.
    """
    items = doc.get("items") or []
    first_item = items[0] if items else {}
    charge_code = first_item.get("charge_code") if isinstance(first_item, dict) else None
    amount = doc.get("total_myr")
    if amount is None:
        amount = doc.get("amount")
    quarantined = doc.get("quarantined")
    status = doc.get("status") or ("quarantined" if quarantined else "accepted")
    return {
        "transaction_id": doc.get("transaction_id"),
        "customer_id": doc.get("customer_id"),
        "amount": amount,
        "charge_code": charge_code,
        "status": status,
        "merchant": doc.get("merchant_id") or doc.get("merchant"),
        "timestamp": _iso(doc.get("timestamp")),
        "created_at": _iso(doc.get("_ingested_at") or doc.get("created_at")),
        "entity": doc.get("entity"),
    }


def _record_ingest_lag(doc: dict[str, Any]) -> None:
    """Push (now − doc.timestamp) ms into ``rule_eval_window`` as a proxy
    for end-to-end ingest latency. Without this nothing ever records into
    ``rule_eval_window`` from the API process, leaving p50/p99 stuck at 0
    in the MSK → ASP → Atlas → change-stream topology.

    Notes:
    - The persisted V3 doc stores ``timestamp`` as a stringified ISO-8601
      (the simulator emits it as a str and the ASP $merge keeps it that
      way), so we have to parse rather than rely on BSON Date.
    - EC2 and the laptop running this API don't share a clock; tiny
      negative lags (sub-second clock skew) are clamped to 0 instead of
      rejected so steady-state samples still feed the percentiles.
    - Absurd values (> _EVAL_LAG_CAP_MS) are dropped so a stale boot
      replay can't poison percentiles for an entire window.
    """
    ts = _as_aware_datetime(doc.get("timestamp"))
    if ts is None:
        return
    lag_ms = (datetime.now(timezone.utc) - ts).total_seconds() * 1000.0
    if lag_ms > _EVAL_LAG_CAP_MS:
        return
    rule_eval_window.record(max(0.0, lag_ms))


async def _watch_transactions(db: AsyncDatabase, stop: asyncio.Event) -> None:
    """Tail `transactions` inserts → `new_txn` SSE + live counter tick."""
    while not stop.is_set():
        try:
            async with await db[TRANSACTIONS].watch(
                pipeline=[{"$match": {"operationType": "insert"}}],
                full_document="updateLookup",
            ) as stream:
                logger.info("sse_tail.transactions.open")
                async for change in stream:
                    if stop.is_set():
                        break
                    doc = change.get("fullDocument") or {}
                    if not doc:
                        continue
                    # Tick the rolling counters so the KPI tiles light up
                    # regardless of where the write originated (ASP/MSK
                    # via change stream, in-process simulator, etc.).
                    try:
                        transactions_window.record()
                        _record_ingest_lag(doc)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("sse_tail.transactions.metric_record_failed", error=str(exc))
                    try:
                        publish_new_transaction(_project_txn(doc))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("sse_tail.transactions.publish_failed", error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("sse_tail.transactions.stream_error", error=str(exc))
            try:
                await asyncio.wait_for(stop.wait(), timeout=_RETRY_BACKOFF_S)
            except asyncio.TimeoutError:
                pass
    logger.info("sse_tail.transactions.closed")


async def _watch_cases(db: AsyncDatabase, stop: asyncio.Event) -> None:
    """Tail `quarantine_cases` inserts/updates → `new_case` / `case_update`.

    Mirrors the projection logic already in `rule_change_watcher` but is
    intentionally publish-only (no embed-sync side effects — that worker
    still owns those writes)."""
    while not stop.is_set():
        try:
            async with await db[QUARANTINE_CASES].watch(full_document="updateLookup") as stream:
                logger.info("sse_tail.cases.open")
                async for change in stream:
                    if stop.is_set():
                        break
                    doc = change.get("fullDocument") or {}
                    op = change.get("operationType")
                    if not doc:
                        continue
                    stripped = {k: v for k, v in doc.items() if k != "_id"}
                    try:
                        if op == "insert":
                            # Tick the rolling counter so "Quarantines/sec"
                            # is non-zero even when rule_change_watcher
                            # isn't running alongside the API.
                            try:
                                quarantine_window.record()
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "sse_tail.cases.metric_record_failed", error=str(exc)
                                )
                            publish_new_case(stripped)
                        else:
                            publish_case_update(stripped)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("sse_tail.cases.publish_failed", error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("sse_tail.cases.stream_error", error=str(exc))
            try:
                await asyncio.wait_for(stop.wait(), timeout=_RETRY_BACKOFF_S)
            except asyncio.TimeoutError:
                pass
    logger.info("sse_tail.cases.closed")


def start(db: AsyncDatabase) -> TailHandle:
    """Launch the two background tails; safe to call once per process."""
    handle = TailHandle()
    handle.tasks.append(
        asyncio.create_task(
            _watch_transactions(db, handle.stop),
            name="sse_tail.transactions",
        )
    )
    handle.tasks.append(
        asyncio.create_task(
            _watch_cases(db, handle.stop),
            name="sse_tail.cases",
        )
    )
    return handle


async def stop(handle: TailHandle) -> None:
    """Signal the tails to exit and await them."""
    handle.stop.set()
    for task in handle.tasks:
        task.cancel()
    await asyncio.gather(*handle.tasks, return_exceptions=True)
