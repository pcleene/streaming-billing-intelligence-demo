"""Unit tests for the simulator's burst ramp schedule.

Locks in the linear ramp-up → hold → ramp-down envelope. No MongoDB or
Kafka involved — `BurstPlan` is pure arithmetic.
"""

from __future__ import annotations

from app.workers.transaction_simulator import BurstPlan


def _plan() -> BurstPlan:
    return BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=300,
        ramp_seconds=30,
        start_at=1_000.0,
    )


def test_phases_in_order() -> None:
    p = _plan()
    assert p.phase(999.0) == "pre"
    assert p.phase(1_000.0) == "ramp_up"
    assert p.phase(1_015.0) == "ramp_up"
    assert p.phase(1_030.0) == "hold"
    assert p.phase(1_329.0) == "hold"
    assert p.phase(1_330.0) == "ramp_down"
    assert p.phase(1_359.0) == "ramp_down"
    assert p.phase(1_360.0) == "post"


def test_tps_endpoints() -> None:
    p = _plan()
    # Pre and post → baseline TPS.
    assert p.tps_at(999.0) == 20
    assert p.tps_at(1_360.0) == 20
    # Hold → target TPS.
    assert p.tps_at(1_100.0) == 200
    # Mid ramp_up at half the ramp window → roughly halfway.
    halfway = p.tps_at(1_015.0)
    assert 100 <= halfway <= 120
    # Mid ramp_down at half → also roughly halfway.
    half_down = p.tps_at(1_345.0)
    assert 100 <= half_down <= 120


def test_is_active_window() -> None:
    p = _plan()
    assert not p.is_active(999.0)
    assert p.is_active(1_000.0)
    assert p.is_active(1_359.999)
    assert not p.is_active(1_360.0)


def test_run_id_unique_per_plan() -> None:
    a = _plan()
    b = _plan()
    assert a.run_id != b.run_id
    assert a.run_id.startswith("burst_")
