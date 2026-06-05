"""PR-11 — `MetricsRecorder` cadence + resilience tests.

Covers two properties:

  - The recorder writes one row per `interval_seconds`. We override the
    interval to 0.1s, run the worker for ~0.35s, then assert at least 3
    rows landed.
  - A `_sample` exception does not kill the worker; subsequent ticks
    still produce rows.

We bypass `connect_mongo` by patching `app.deps._db` to point at a
`FakeDB` before constructing the recorder. This mirrors the strategy
used in `tests/test_workers/test_case_lifecycle_worker.py`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import app.deps as deps
from app.workers.metrics_recorder import MetricsRecorder
from tests._fakes import FakeDB


def _patch_db(db: FakeDB):
    """Return a context manager that swaps `app.deps._db` for `db`."""
    return patch.object(deps, "_db", db)


@pytest.mark.asyncio
async def test_metrics_recorder_emits_one_row_per_interval() -> None:
    """Run the recorder for ~0.35s with a 0.1s interval; expect >= 3
    rows in `system_metrics`."""
    db = FakeDB()

    with _patch_db(db):
        rec = MetricsRecorder()
        # Bypass the floor of 5 imposed in __init__; we want sub-second.
        rec._interval = 0.1

        async def _runner() -> None:
            try:
                await asyncio.wait_for(rec.run(), timeout=0.35)
            except asyncio.TimeoutError:
                pass

        await _runner()
        await rec.shutdown()

    count = await db["system_metrics"].count_documents({})
    # 0.35s / 0.1s tick = 3-4 ticks; allow some scheduler jitter.
    assert count >= 3, f"expected >= 3 rows, got {count}"


@pytest.mark.asyncio
async def test_metrics_recorder_continues_through_sample_failure() -> None:
    """If `_sample` raises on the first call, the worker logs and
    continues — the next tick succeeds and writes a row."""
    db = FakeDB()

    # Build a `_sample` side-effect that raises once, then returns a
    # valid doc forever after.
    call_state = {"calls": 0}

    async def flaky_sample(_txns) -> dict:
        call_state["calls"] += 1
        if call_state["calls"] == 1:
            raise RuntimeError("simulated transient failure")
        return {
            "recorded_at": None,
            "mode": "steady",
            "burst_run_id": None,
            "observed_tps": 0.0,
        }

    with _patch_db(db):
        rec = MetricsRecorder()
        rec._interval = 0.05
        # Patch the bound method on the instance.
        rec._sample = flaky_sample  # type: ignore[assignment]

        async def _runner() -> None:
            try:
                await asyncio.wait_for(rec.run(), timeout=0.30)
            except asyncio.TimeoutError:
                pass

        await _runner()
        await rec.shutdown()

    assert call_state["calls"] >= 2, "expected >= 2 _sample calls"
    count = await db["system_metrics"].count_documents({})
    assert count >= 1, "first failure must not stop subsequent writes"
