"""PR-11 — additional `MetricsAggregatorService.get_burst_status` tests.

These tests pin the PR-11 spec contract (samples list, summary
threshold-breach count, db-vs-collection wiring, hard limit cap) on
top of the sister-PR baseline already covered by
`tests/test_metrics_burst_status.py`.

Pure-Python tests against `tests._fakes.FakeDB`. The service is
constructed via the new `db=` keyword introduced in PR-11; the
existing `system_metrics_collection=` kwarg is left untouched.

Tests:

  - explicit `run_id` returns matching rows oldest→newest
  - latest run resolution when no run_id is passed
  - empty/no-burst envelope is the well-defined zero shape
  - peak_observed_tps tracks the max of `observed_tps`
  - `rule_eval_p99_threshold_breaches` counts rows with
    `rule_eval_p99_ms > 200`
  - `limit` caps the number of returned samples
  - missing db handle raises a clear error
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.metrics_aggregator_service import MetricsAggregatorService
from tests._fakes import FakeDB


_BASE = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds_offset: int) -> datetime:
    return _BASE + timedelta(seconds=seconds_offset)


def _row(
    *,
    seconds_offset: int,
    mode: str = "burst",
    observed_tps: float = 100.0,
    burst_run_id: str | None = None,
    rule_eval_p99_ms: float = 0.0,
    p99_ms_ingest: float = 0.0,
) -> dict[str, Any]:
    """Build a system_metrics-shaped doc compatible with what
    `app.workers.metrics_recorder._sample` writes to Mongo."""
    return {
        "recorded_at": _ts(seconds_offset),
        "mode": mode,
        "burst_run_id": burst_run_id,
        "observed_tps": observed_tps,
        "p50_ms_ingest": 0.0,
        "p99_ms_ingest": p99_ms_ingest,
        "quarantine_per_sec": 0.0,
        "rule_eval_p99_ms": rule_eval_p99_ms,
        "txns_in_window": int(observed_tps * 60),
        "cases_in_window": 0,
    }


def _make_service(db: FakeDB | None) -> MetricsAggregatorService:
    """Service constructed via the new `db=` kwarg (PR-11 path)."""
    repo = AsyncMock()
    repo.aggregate = AsyncMock(return_value=[])
    return MetricsAggregatorService(repo, db=db)


# --- 1. Explicit run_id returns matching rows, oldest→newest --------


@pytest.mark.asyncio
async def test_get_burst_status_explicit_run_id_returns_matching_rows() -> None:
    """Seed 5 rows for run abc + 3 for run xyz; assert samples are the
    5 abc rows ordered oldest→newest by recorded_at."""
    db = FakeDB()
    coll = db["system_metrics"]
    # 5 rows for abc — increasing recorded_at offsets 0..240 (60s apart).
    for i in range(5):
        await coll.insert_one(
            _row(seconds_offset=i * 60, burst_run_id="abc")
        )
    # 3 rows for xyz interleaved at later offsets.
    for i in range(3):
        await coll.insert_one(
            _row(seconds_offset=300 + i * 60, burst_run_id="xyz")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="abc")

    assert out["run_id"] == "abc"
    samples = out["samples"]
    assert len(samples) == 5
    # Oldest → newest by recorded_at.
    times = [s["recorded_at"] for s in samples]
    assert times == sorted(times)
    assert times[0] == _ts(0)
    assert times[-1] == _ts(240)
    # Every sample is from the requested run.
    for s in samples:
        assert s["burst_run_id"] == "abc"


# --- 2. Resolve latest run when no run_id ---------------------------


@pytest.mark.asyncio
async def test_get_burst_status_resolves_latest_run_when_no_id() -> None:
    """Two run_ids seeded at different times: the resolver picks the
    one whose most-recent burst row is newest."""
    db = FakeDB()
    coll = db["system_metrics"]
    # run "old" — recorded earlier.
    for i in range(3):
        await coll.insert_one(
            _row(seconds_offset=i * 60, burst_run_id="old")
        )
    # run "new" — strictly later recorded_at across all rows.
    for i in range(3):
        await coll.insert_one(
            _row(seconds_offset=600 + i * 60, burst_run_id="new")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status()

    assert out["run_id"] == "new"
    # All surfaced samples must belong to the resolved run.
    for s in out["samples"]:
        assert s["burst_run_id"] == "new"


# --- 3. No burst at all → empty envelope ----------------------------


@pytest.mark.asyncio
async def test_get_burst_status_no_burst_returns_none_run_id_empty_samples() -> None:
    """All rows have burst_run_id=None and mode='steady' → envelope
    reports run_id=None, samples=[], and the zero-shape summary."""
    db = FakeDB()
    coll = db["system_metrics"]
    for i in range(4):
        await coll.insert_one(
            _row(seconds_offset=i * 60, mode="steady",
                 observed_tps=10.0, burst_run_id=None)
        )

    svc = _make_service(db)
    out = await svc.get_burst_status()

    assert out["run_id"] is None
    assert out["samples"] == []
    summary = out["summary"]
    # All numeric summary fields collapse to zero; date fields to None.
    assert summary["sample_count"] == 0
    assert summary["peak_observed_tps"] == 0.0
    assert summary["peak_p99_ms"] == 0.0
    assert summary["rule_eval_p99_threshold_breaches"] == 0
    assert summary["duration_seconds"] == 0.0
    assert summary["started_at"] is None
    assert summary["ended_at"] is None


# --- 4. peak_observed_tps tracks max(observed_tps) ------------------


@pytest.mark.asyncio
async def test_get_burst_status_summary_peak_tps() -> None:
    """observed_tps `[10, 50, 200, 30]` -> peak_observed_tps == 200."""
    db = FakeDB()
    coll = db["system_metrics"]
    values = [10.0, 50.0, 200.0, 30.0]
    for i, v in enumerate(values):
        await coll.insert_one(
            _row(seconds_offset=i * 60, observed_tps=v, burst_run_id="R")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="R")

    assert out["summary"]["peak_observed_tps"] == pytest.approx(200.0, abs=0.01)


# --- 5. rule_eval_p99_threshold_breaches count -----------------------


@pytest.mark.asyncio
async def test_get_burst_status_threshold_breaches() -> None:
    """rule_eval_p99_ms `[150, 220, 180, 350]` → 2 breaches (>200)."""
    db = FakeDB()
    coll = db["system_metrics"]
    p99s = [150.0, 220.0, 180.0, 350.0]
    for i, v in enumerate(p99s):
        await coll.insert_one(
            _row(seconds_offset=i * 60,
                 burst_run_id="R", rule_eval_p99_ms=v)
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="R")

    assert out["summary"]["rule_eval_p99_threshold_breaches"] == 2


# --- 6. limit caps the sample list ----------------------------------


@pytest.mark.asyncio
async def test_get_burst_status_respects_limit() -> None:
    """Seed 100 rows for one run; ask for 10 → exactly 10 samples back."""
    db = FakeDB()
    coll = db["system_metrics"]
    for i in range(100):
        await coll.insert_one(
            _row(seconds_offset=i * 60,
                 observed_tps=float(i + 1), burst_run_id="R")
        )

    svc = _make_service(db)
    out = await svc.get_burst_status(run_id="R", limit=10)

    assert len(out["samples"]) == 10
    # The capped window is the *newest* 10 (oldest→newest after reverse).
    # The newest row had observed_tps=100; the 10th-newest had 91.
    observed = [s["observed_tps"] for s in out["samples"]]
    assert observed == sorted(observed)
    assert observed[-1] == pytest.approx(100.0, abs=0.01)
    assert observed[0] == pytest.approx(91.0, abs=0.01)


# --- 7. Missing db handle raises ------------------------------------


@pytest.mark.asyncio
async def test_get_burst_status_raises_when_no_db() -> None:
    """Constructed without `db=` (and without
    `system_metrics_collection=`), calling get_burst_status raises a
    clear runtime error."""
    repo = AsyncMock()
    svc = MetricsAggregatorService(repo, db=None)
    with pytest.raises(RuntimeError) as exc_info:
        await svc.get_burst_status()
    # Error text mentions the missing dependency clearly.
    msg = str(exc_info.value).lower()
    assert "db" in msg or "system_metrics" in msg
