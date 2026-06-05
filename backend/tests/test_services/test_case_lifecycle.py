"""Unit tests for `app.services.case_lifecycle` pure helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.case_lifecycle import (
    _SEVERITY_RANK,
    _SLA_TARGET_MINUTES,
    apply_sla_tick,
    compute_priority,
    initial_sla,
    open_lifecycle_event,
    revenue_impact_from_summary,
    transition_lifecycle_event,
)


# --- compute_priority ----------------------------------------------------


def test_compute_priority_critical_high_amount_lands_in_p0() -> None:
    score, band, drivers = compute_priority(
        severity="critical",
        amount_at_risk_myr=10_000,
        customer_lifetime_quarantine_count=5,
        is_commercial=True,
    )
    assert band == "P0"
    assert score >= 0.85
    assert "severity:critical" in drivers
    assert "commercial_account" in drivers


def test_compute_priority_low_zero_amount_lands_in_p3() -> None:
    score, band, drivers = compute_priority(
        severity="low",
        amount_at_risk_myr=0.0,
        customer_lifetime_quarantine_count=0,
        is_commercial=False,
    )
    assert band == "P3"
    assert score < 0.40


def test_compute_priority_medium_with_repeat_offender_climbs_to_p2_or_p1() -> None:
    # A medium-severity case with a repeat offender and a non-trivial
    # amount-at-risk should clear the P2 threshold (0.40).
    score, band, drivers = compute_priority(
        severity="medium",
        amount_at_risk_myr=2_500.0,
        customer_lifetime_quarantine_count=3,
        is_commercial=False,
    )
    assert band in ("P1", "P2")
    assert "repeat_offender:3" in drivers


def test_compute_priority_score_is_capped_at_one() -> None:
    score, band, drivers = compute_priority(
        severity="critical",
        amount_at_risk_myr=10_000_000.0,
        customer_lifetime_quarantine_count=999,
        is_commercial=True,
    )
    assert score <= 1.0
    assert band == "P0"


def test_compute_priority_unknown_severity_defaults_to_low_rank() -> None:
    score, band, drivers = compute_priority(severity="weird")
    # Unknown severity falls back to a "low" baseline rank (0.25).
    # Expected score: 0.55 * 0.25 = 0.1375, well below P3 threshold (0.40).
    assert band == "P3"
    # Should land near a low baseline.
    low_score, _, _ = compute_priority(severity="low")
    assert score == pytest.approx(low_score, abs=1e-4)


# --- initial_sla ---------------------------------------------------------


def test_initial_sla_critical_30_minutes() -> None:
    opened_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    sla = initial_sla(severity="critical", opened_at=opened_at)
    assert (sla["target_resolution_at"] - opened_at).total_seconds() == 30 * 60
    assert sla["minutes_to_breach"] == 30.0
    assert sla["is_breached"] is False
    assert sla["breached_at"] is None
    assert sla["elapsed_minutes"] == 0.0


def test_initial_sla_other_buckets() -> None:
    opened_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for sev, expected_minutes in (
        ("high", 240),
        ("medium", 1_440),
        ("low", 4_320),
    ):
        sla = initial_sla(severity=sev, opened_at=opened_at)
        delta = sla["target_resolution_at"] - opened_at
        assert delta.total_seconds() == expected_minutes * 60
        assert sla["minutes_to_breach"] == float(expected_minutes)
        assert _SLA_TARGET_MINUTES[sev] == expected_minutes


# --- revenue_impact_from_summary -----------------------------------------


def test_revenue_impact_high_severity_uses_80pct_confidence() -> None:
    out = revenue_impact_from_summary(
        transaction_summary={"total_myr": 1000.0}, severity="high"
    )
    assert out["amount_at_risk_myr"] == 1000.0
    assert out["revenue_protected_if_correct_myr"] == 800.0
    assert out["false_positive_ops_cost_myr"] == 12.0
    assert out["ev_resolution_value_myr"] == 788.0


def test_revenue_impact_medium_uses_50pct_confidence() -> None:
    out = revenue_impact_from_summary(
        transaction_summary={"total_myr": 1000.0}, severity="medium"
    )
    assert out["amount_at_risk_myr"] == 1000.0
    assert out["revenue_protected_if_correct_myr"] == 500.0
    assert out["false_positive_ops_cost_myr"] == 12.0
    assert out["ev_resolution_value_myr"] == 488.0


def test_revenue_impact_no_summary_zero_amount() -> None:
    out = revenue_impact_from_summary(transaction_summary=None, severity="medium")
    assert out["amount_at_risk_myr"] == 0.0
    assert out["revenue_protected_if_correct_myr"] >= 0
    assert out["false_positive_ops_cost_myr"] >= 0
    # ev can be negative when cost > protected; we just require all
    # nominal numbers are present and the at-risk total is zero.
    assert "ev_resolution_value_myr" in out


# --- open_lifecycle_event / transition_lifecycle_event -------------------


def test_open_lifecycle_event_shape() -> None:
    evt = open_lifecycle_event(actor_id="system", rule_types_fired=["velocity_anomaly"])
    assert evt["from_status"] is None
    assert evt["to_status"] == "open"
    assert evt["actor_type"] == "system"
    assert evt["actor_id"] == "system"
    assert evt["rule_types_fired"] == ["velocity_anomaly"]
    assert isinstance(evt["at"], datetime)
    assert evt["at"].tzinfo is not None


def test_transition_lifecycle_event_shape() -> None:
    evt = transition_lifecycle_event(
        from_status="open",
        to_status="under_review",
        actor_id="alice",
        actor_type="analyst",
        note="checking",
    )
    assert evt["from_status"] == "open"
    assert evt["to_status"] == "under_review"
    assert evt["actor_id"] == "alice"
    assert evt["actor_type"] == "analyst"
    assert evt["note"] == "checking"
    assert isinstance(evt["at"], datetime)
    assert evt["at"].tzinfo is not None


# --- apply_sla_tick ------------------------------------------------------


def test_apply_sla_tick_not_breached_yet() -> None:
    opened_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    sla = initial_sla(severity="medium", opened_at=opened_at)
    tick = apply_sla_tick(
        sla=sla, opened_at=opened_at, now=opened_at + timedelta(minutes=10)
    )
    assert tick["elapsed_minutes"] == 10.0
    assert tick["minutes_to_breach"] == 1430.0
    assert tick["is_breached"] is False
    assert tick["breached_at"] is None


def test_apply_sla_tick_just_breached_sets_breached_at() -> None:
    opened_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    sla = initial_sla(severity="critical", opened_at=opened_at)
    now = opened_at + timedelta(minutes=45)
    tick1 = apply_sla_tick(sla=sla, opened_at=opened_at, now=now)
    assert tick1["is_breached"] is True
    assert tick1["breached_at"] == now


def test_apply_sla_tick_already_breached_keeps_original_breached_at() -> None:
    opened_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    some_old_time = opened_at + timedelta(minutes=31)
    sla = {
        "target_resolution_at": opened_at + timedelta(minutes=30),
        "elapsed_minutes": 31.0,
        "minutes_to_breach": -1.0,
        "is_breached": True,
        "breached_at": some_old_time,
    }
    later_now = opened_at + timedelta(minutes=120)
    tick = apply_sla_tick(sla=sla, opened_at=opened_at, now=later_now)
    assert tick["is_breached"] is True
    assert tick["breached_at"] == some_old_time


# --- module-level constants sanity --------------------------------------


def test_severity_rank_constants_present() -> None:
    # Sanity: ensure the severity rank table covers the four canonical buckets.
    assert _SEVERITY_RANK["critical"] == 1.0
    assert _SEVERITY_RANK["high"] == 0.75
    assert _SEVERITY_RANK["medium"] == 0.50
    assert _SEVERITY_RANK["low"] == 0.25
