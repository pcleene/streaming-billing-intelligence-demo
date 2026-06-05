"""PR-11 — Simulator burst spec-gate tests.

The PR-11 master-plan spec demands:

  > Simulator hits >= 90% of target_tps for >= 25 of 30 seconds; keeps
  > rule_eval_p99_ms <= 200 throughout.
  > system_metrics rows are produced once per minute regardless of load.

These tests lock in the deterministic `BurstPlan` math (no real loop,
no Kafka, no Mongo) plus the simulator's CLI flag surface and event-
stamping behaviour.
"""

from __future__ import annotations

import time

from app.workers.transaction_simulator import (
    BurstPlan,
    Simulator,
    _build_arg_parser,
)


# ----------------------------------------------------------------------
# `BurstPlan` envelope — locks in the >=90% / >=25 of 30 spec gate
# ----------------------------------------------------------------------


def test_burst_plan_holds_at_target_for_majority_of_window() -> None:
    """Spec: >= 25 of 30 hold-window seconds at >= 90% of target_tps.

    With ramp_seconds=2 + hold_seconds=30, sampling 0..34 covers the
    entire ramp_up + hold + start of ramp_down. The hold span alone
    delivers 30 samples at exactly target_tps, comfortably clearing
    the >= 25 gate.
    """
    t0 = 1_000.0
    target = 200
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=target,
        duration_seconds=30,  # hold window
        ramp_seconds=2,
        start_at=t0,
    )

    threshold = 0.9 * target  # 180
    samples = [plan.tps_at(t0 + n) for n in range(35)]
    above = sum(1 for tps in samples if tps >= threshold)

    assert above >= 25, (
        f"only {above} of {len(samples)} samples hit >= {threshold} TPS"
    )


def test_burst_plan_ramps_smoothly_into_hold() -> None:
    """Mid-ramp lies between baseline and target; end-of-ramp clamps
    to target."""
    t0 = 1_000.0
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=30,
        ramp_seconds=2,
        start_at=t0,
    )

    # Mid-ramp (t0 + 1) is half-way through a 2s ramp → ~110 TPS.
    mid = plan.tps_at(t0 + 1)
    assert plan.baseline_tps < mid < plan.target_tps

    # End of ramp (t0 + 2) → first hold sample, must be at target.
    assert plan.tps_at(t0 + 2) >= plan.target_tps


def test_burst_plan_returns_to_baseline_after_total_window() -> None:
    """After ramp_up + hold + ramp_down + 1s the plan is no longer
    active and `tps_at` falls back to baseline."""
    t0 = 1_000.0
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=30,
        ramp_seconds=2,
        start_at=t0,
    )

    after = t0 + plan.ramp_seconds + plan.duration_seconds + plan.ramp_seconds + 1
    assert plan.is_active(after) is False
    assert plan.tps_at(after) == plan.baseline_tps


def test_burst_plan_phase_transitions() -> None:
    """Phase progresses pre → ramp_up → hold → ramp_down → post over
    the full window."""
    t0 = 1_000.0
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=30,
        ramp_seconds=2,
        start_at=t0,
    )

    assert plan.phase(t0 - 1) == "pre"
    assert plan.phase(t0) == "ramp_up"
    assert plan.phase(t0 + plan.ramp_seconds) == "hold"
    assert plan.phase(t0 + plan.ramp_seconds + plan.duration_seconds) == "ramp_down"
    end = t0 + plan.ramp_seconds + plan.duration_seconds + plan.ramp_seconds
    assert plan.phase(end) == "post"


def test_burst_run_id_is_stable_per_plan() -> None:
    """Each `BurstPlan` instance picks its own `run_id`; repeated
    access on a single plan returns the same id."""
    a = BurstPlan(
        baseline_tps=20, target_tps=200,
        duration_seconds=30, ramp_seconds=2, start_at=0.0,
    )
    b = BurstPlan(
        baseline_tps=20, target_tps=200,
        duration_seconds=30, ramp_seconds=2, start_at=0.0,
    )

    # Per-plan stability.
    assert a.run_id == a.run_id
    # Inter-plan uniqueness.
    assert a.run_id != b.run_id
    # Convention check.
    assert a.run_id.startswith("burst_")


# ----------------------------------------------------------------------
# Simulator argparse surface — `--burst*` flag plumbing
# ----------------------------------------------------------------------


def test_simulator_argparse_exposes_burst_flags() -> None:
    """`--burst`, `--burst-target-tps`, `--burst-duration-seconds`
    must all be reachable through the parser builder."""
    parser = _build_arg_parser()
    ns = parser.parse_args(
        ["--burst", "--burst-target-tps", "200", "--burst-duration-seconds", "30"]
    )

    assert ns.burst is True
    assert ns.burst_target_tps == 200
    assert ns.burst_duration_seconds == 30
    # `--burst-ramp-seconds` should also be on the namespace (with default).
    assert hasattr(ns, "burst_ramp_seconds")


def test_simulator_argparse_defaults_off_when_no_args() -> None:
    """Without `--burst`, the namespace flags `burst=False` and the
    target/duration fall back to settings defaults."""
    parser = _build_arg_parser()
    ns = parser.parse_args([])

    assert ns.burst is False
    assert isinstance(ns.burst_target_tps, int)
    assert isinstance(ns.burst_duration_seconds, int)


# ----------------------------------------------------------------------
# Event stamping — `metadata.burst_run_id` propagation through Simulator
# ----------------------------------------------------------------------


def _customer() -> dict:
    return {
        "customer_id": "cust_burst_1",
        "customer_type": "residential",
        "address": {"state": "Selangor"},
        "subscriptions": [
            {"package_name": "Premium", "status": "active", "monthly_fee_myr": 199.0}
        ],
    }


def test_simulator_stamps_burst_run_id_when_burst_active() -> None:
    """When the simulator's burst plan is active, every emitted event
    carries `metadata.burst_run_id` matching the plan."""
    now = time.time()
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=30,
        ramp_seconds=2,
        start_at=now - 1,  # already ramping
    )

    sim = Simulator.__new__(Simulator)
    sim._burst = plan

    event = sim._build_event(_customer(), anomaly=False, now=now)

    assert event["metadata"]["burst_run_id"] == plan.run_id
    assert event["metadata"]["burst_phase"] in {"ramp_up", "hold", "ramp_down"}


def test_simulator_omits_burst_metadata_outside_burst_window() -> None:
    """Outside the burst window, events do not carry a `burst_run_id`
    — the plan is one-shot and metadata stays clean afterwards."""
    plan = BurstPlan(
        baseline_tps=20,
        target_tps=200,
        duration_seconds=30,
        ramp_seconds=2,
        start_at=1_000.0,
    )
    # Sample well before the burst window opens.
    event_pre = Simulator.__new__(Simulator)
    event_pre._burst = plan
    pre = event_pre._build_event(_customer(), anomaly=False, now=500.0)
    assert "burst_run_id" not in pre.get("metadata", {})

    # Sample well after the burst window closes.
    post = event_pre._build_event(_customer(), anomaly=False, now=2_000.0)
    assert "burst_run_id" not in post.get("metadata", {})
