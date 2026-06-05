"""Server-Sent Events for live dashboard tiles + change-stream broadcasts.

A single multiplexed /api/stream endpoint emits:
  - metric_tick  (every 1s)
  - new_txn      (pushed by the simulator/feature-engineer)
  - new_case     (pushed by the change-stream watcher on quarantine_cases)
  - case_update  (pushed on disposition)
  - rule_change  (pushed by rule_change_watcher)
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.core.constants import (
    SSE_CASE_UPDATE,
    SSE_METRIC_TICK,
    SSE_NEW_CASE,
    SSE_NEW_TXN,
    SSE_RULE_CHANGE,
)
from app.core.logging import get_logger
from app.core.metrics import snapshot

logger = get_logger(__name__)
router = APIRouter(tags=["stream"])


# In-process pubsub — workers push, route subscribers drain.
_subscribers: set[asyncio.Queue] = set()


def publish(event: str, data: dict) -> None:
    """Workers call this to broadcast to all SSE subscribers."""
    payload = {"event": event, "data": data}
    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


async def _generator() -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(queue)
    try:
        # Send an immediate metrics frame so the UI lights up.
        yield {"event": SSE_METRIC_TICK, "data": json.dumps(snapshot())}
        last_metrics = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield {"event": payload["event"], "data": json.dumps(payload["data"], default=str)}
            except asyncio.TimeoutError:
                pass
            now = loop.time()
            if now - last_metrics >= 1.0:
                yield {"event": SSE_METRIC_TICK, "data": json.dumps(snapshot())}
                last_metrics = now
    finally:
        _subscribers.discard(queue)


@router.get("/api/stream")
async def stream() -> EventSourceResponse:
    return EventSourceResponse(
        _generator(),
        ping=15,
        headers={"Cache-Control": "no-cache, no-transform"},
    )


# Convenience wrappers used by workers ----------------------------------
def publish_metrics() -> None:
    publish(SSE_METRIC_TICK, snapshot())


def publish_new_transaction(txn: dict) -> None:
    publish(SSE_NEW_TXN, txn)


def publish_new_case(case: dict) -> None:
    publish(SSE_NEW_CASE, case)


def publish_case_update(case: dict) -> None:
    publish(SSE_CASE_UPDATE, case)


def publish_rule_changed(rule: dict) -> None:
    publish(SSE_RULE_CHANGE, rule)
