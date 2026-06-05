"""PR-11 — `MetricsRecorder` worker spec-gate tests.

Pure-Python tests against `tests._fakes.FakeDB` plus monkeypatched
module-level rolling windows. We never need a live Mongo or Kafka:

  - `_sample(txns)` is exercised directly with a FakeDB collection.
  - `run()` is wired to exit after one tick by pre-setting `_stop`
    so the `asyncio.wait_for` short-circuits on the first sleep.

Spec the tests lock in (master plan PR-11):

  - `mode` derives from observed_tps + presence of a recent
    `metadata.burst_run_id`: idle / steady / burst.
  - `burst_run_id` propagates from the most-recent in-window txn.
  - `rule_eval_p99_ms` and `p50_ms_ingest` come from
    `app.core.metrics.rule_eval_window`.
  - `txns_in_window` only counts rows whose timestamp lies inside the
    interval (older rows excluded).
  - `run()` writes exactly one `system_metrics` doc per tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.workers import metrics_recorder as mr_mod
from app.workers.metrics_recorder import MetricsRecorder
from tests._fakes import FakeDB


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class _StubWindow:
    """Drop-in for `RollingWindow` exposing only the methods the
    recorder calls."""

    def __init__(self, *, rate: float = 0.0, p99: float = 0.0, p50: float = 0.0) -> None:
        self._rate = rate
        self._p99 = p99
        self._p50 = p50

    def rate_per_sec(self) -> float:
        return self._rate

    def percentile_ms(self, p: float = 0.99) -> float:
        if abs(p - 0.99) < 1e-9:
            return self._p99
        if abs(p - 0.50) < 1e-9:
            return self._p50
        return 0.0


def _patch_windows(
    monkeypatch: pytest.MonkeyPatch,
    *,
    txns_rate: float = 0.0,
    rule_p99: float = 0.0,
    rule_p50: float = 0.0,
    quarantine_rate: float = 0.0,
) -> None:
    """Replace the module-level rolling windows on `metrics_recorder`
    with deterministic stubs for the duration of one test."""
    monkeypatch.setattr(
        mr_mod, "transactions_window", _StubWindow(rate=txns_rate)
    )
    monkeypatch.setattr(
        mr_mod, "rule_eval_window", _StubWindow(p99=rule_p99, p50=rule_p50)
    )
    monkeypatch.setattr(
        mr_mod, "quarantine_window", _StubWindow(rate=quarantine_rate)
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_txn(
    db: FakeDB,
    *,
    timestamp: datetime,
    burst_run_id: str | None = None,
) -> None:
    """Insert a `transactions` row with an ISO timestamp string —
    matches what the simulator emits on the wire."""
    doc: dict = {"timestamp": timestamp.isoformat()}
    if burst_run_id is not None:
        doc["metadata"] = {"burst_run_id": burst_run_id}
    db["transactions"]._docs.append(doc)  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# `_sample` mode classification
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_no_burst_marks_idle_when_observed_tps_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty txns + zero observed TPS → mode='idle', no burst id."""
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=0.0)

    rec = MetricsRecorder()
    doc = await rec._sample(db["transactions"])

    assert doc["mode"] == "idle"
    assert doc["burst_run_id"] is None
    assert doc["observed_tps"] == 0.0


@pytest.mark.asyncio
async def test_sample_no_burst_marks_steady_when_observed_tps_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty txns but observed TPS > 0 → mode='steady'."""
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=5.0)

    rec = MetricsRecorder()
    doc = await rec._sample(db["transactions"])

    assert doc["mode"] == "steady"
    assert doc["burst_run_id"] is None
    assert doc["observed_tps"] == pytest.approx(5.0, abs=0.01)


@pytest.mark.asyncio
async def test_sample_with_burst_marks_burst_and_propagates_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recent in-window txn carrying `metadata.burst_run_id` flips
    `mode` to 'burst' and the id is copied through verbatim."""
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=180.0)

    # Recent timestamp comfortably inside the default 60s interval.
    now = _utcnow()
    _seed_txn(db, timestamp=now, burst_run_id="run_abc")

    # FakeDB's `_project` doesn't honour dotted-path includes
    # (`metadata.burst_run_id`), so override `find_one` to return the
    # nested shape the recorder reads via `.get("metadata", {})`.
    txns = db["transactions"]
    real_find_one = txns.find_one

    async def _find_one(flt=None, projection=None, *, sort=None):
        # Strip the dotted projection so we get the full doc.
        return await real_find_one(flt, None, sort=sort)

    monkeypatch.setattr(txns, "find_one", _find_one)

    rec = MetricsRecorder()
    doc = await rec._sample(txns)

    assert doc["mode"] == "burst"
    assert doc["burst_run_id"] == "run_abc"


# ----------------------------------------------------------------------
# `_sample` numeric fields from rolling windows
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_writes_p99_and_p50_from_rule_eval_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`rule_eval_p99_ms` and `p50_ms_ingest` mirror
    `rule_eval_window.percentile_ms(...)`."""
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=0.0, rule_p99=187.4, rule_p50=30.2)

    rec = MetricsRecorder()
    doc = await rec._sample(db["transactions"])

    assert doc["rule_eval_p99_ms"] == pytest.approx(187.4, abs=0.05)
    assert doc["p99_ms_ingest"] == pytest.approx(187.4, abs=0.05)
    assert doc["p50_ms_ingest"] == pytest.approx(30.2, abs=0.05)


@pytest.mark.asyncio
async def test_sample_counts_txns_in_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seven recent txns + three older-than-window → `txns_in_window == 7`."""
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=0.0)

    rec = MetricsRecorder()
    interval = rec._interval

    now = _utcnow()
    # 7 inside the window (recent).
    for i in range(7):
        _seed_txn(db, timestamp=now - timedelta(seconds=i))
    # 3 outside the window (well past `interval` seconds ago).
    for i in range(3):
        _seed_txn(db, timestamp=now - timedelta(seconds=interval + 60 + i))

    doc = await rec._sample(db["transactions"])

    assert doc["txns_in_window"] == 7


# ----------------------------------------------------------------------
# `run()` loop writes exactly one doc per tick
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_loop_writes_one_sample_per_tick_then_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire the loop to exit after a single iteration via `_stop` —
    assert exactly one `insert_one` call landed on `system_metrics`.

    This doubles as the "writes once per interval regardless of load"
    spec assertion the master plan calls out.
    """
    db = FakeDB()
    _patch_windows(monkeypatch, txns_rate=10.0)

    # Patch `get_db` inside the recorder module so `run()` finds our FakeDB.
    monkeypatch.setattr(mr_mod, "get_db", lambda: db)

    rec = MetricsRecorder()

    # Short-circuit the per-tick sleep: stub `asyncio.wait_for` so that
    # after the first iteration's body runs, it sets `_stop` and the
    # `while not self._stop.is_set()` predicate exits the loop.
    import asyncio as _asyncio_mod

    async def _fake_wait_for(awaitable, timeout):  # noqa: ARG001
        rec._stop.set()
        # Cancel the underlying coroutine cleanly so it doesn't leak.
        if hasattr(awaitable, "close"):
            awaitable.close()
        return None

    monkeypatch.setattr(mr_mod.asyncio, "wait_for", _fake_wait_for)

    await rec.run()

    inserted = db["system_metrics"]._docs  # type: ignore[attr-defined]
    assert len(inserted) == 1
    sample = inserted[0]
    # Sample shape sanity-check.
    assert sample["mode"] == "steady"
    assert "recorded_at" in sample
    assert "rule_eval_p99_ms" in sample
