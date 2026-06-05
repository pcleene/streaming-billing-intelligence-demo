"""Case lifecycle helpers — pure functions used by `quarantine_case_repo`
and `case_lifecycle_worker` (PR-7).

Kept as standalone functions (not methods) so they can be unit-tested
without DB plumbing. They take only the fields they need from the case
dict and return either the updated dict fragments or a brand-new
sub-document.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.common import Severity

# Severity → SLA target (minutes from open_at to target_resolution_at).
# Tightened for critical to match Acme's "high-impact must page within
# the hour" SLO; medium / low align with the customer-care queue.
_SLA_TARGET_MINUTES: dict[str, int] = {
    Severity.CRITICAL.value: 30,
    Severity.HIGH.value:     240,    # 4h
    Severity.MEDIUM.value:   1_440,  # 24h
    Severity.LOW.value:      4_320,  # 72h
}

# Severity → numeric rank for priority scoring.
_SEVERITY_RANK: dict[str, float] = {
    Severity.CRITICAL.value: 1.0,
    Severity.HIGH.value:     0.75,
    Severity.MEDIUM.value:   0.50,
    Severity.LOW.value:      0.25,
}

# Score thresholds for priority bands. Tuned so the natural cuts land
# where the SLA buckets do: critical → P0, high+big$$ → P0, etc.
_BAND_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.85, "P0"),
    (0.65, "P1"),
    (0.40, "P2"),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _severity_value(sev: Any) -> str:
    """Pydantic enums sometimes survive into dicts as the Enum itself; the
    repo layer always serialises with `use_enum_values=True`, but defensive
    coercion costs nothing and keeps tests / backfill robust.
    """
    if hasattr(sev, "value"):
        return sev.value
    return str(sev)


def compute_priority(
    *,
    severity: Any,
    amount_at_risk_myr: float = 0.0,
    customer_lifetime_quarantine_count: int = 0,
    is_commercial: bool = False,
) -> tuple[float, str, list[str]]:
    """Score a case + classify it into a priority band.

    Returns `(priority_score, priority_band, drivers)`. `drivers` is a
    short audit trail naming each contributor so the UI can show why
    a case landed in P0/P1/etc.

    Score is a weighted blend (capped at 1.0):
      0.55 · severity_rank
    + 0.30 · amount_factor (saturates at 5000 MYR)
    + 0.10 · repeat_offender (capped at 5 prior cases)
    + 0.05 · commercial bump
    """
    sev = _severity_value(severity)
    sev_rank = _SEVERITY_RANK.get(sev, 0.25)
    drivers: list[str] = [f"severity:{sev}"]

    amount_factor = min(1.0, max(0.0, amount_at_risk_myr) / 5_000.0)
    if amount_at_risk_myr >= 1_000:
        drivers.append(f"amount_at_risk:{round(amount_at_risk_myr, 2)}")

    repeat_factor = min(1.0, max(0, customer_lifetime_quarantine_count) / 5.0)
    if customer_lifetime_quarantine_count >= 2:
        drivers.append(f"repeat_offender:{customer_lifetime_quarantine_count}")

    commercial_factor = 1.0 if is_commercial else 0.0
    if is_commercial:
        drivers.append("commercial_account")

    score = (
        0.55 * sev_rank
        + 0.30 * amount_factor
        + 0.10 * repeat_factor
        + 0.05 * commercial_factor
    )
    score = round(min(1.0, score), 4)

    band = "P3"
    for threshold, label in _BAND_THRESHOLDS:
        if score >= threshold:
            band = label
            break
    return score, band, drivers


def initial_sla(*, severity: Any, opened_at: datetime | None = None) -> dict:
    """Build the initial SLA fragment for a freshly-opened case."""
    sev = _severity_value(severity)
    target_minutes = _SLA_TARGET_MINUTES.get(sev, 1_440)
    opened = opened_at or _utcnow()
    target = opened + timedelta(minutes=target_minutes)
    return {
        "target_resolution_at": target,
        "elapsed_minutes": 0.0,
        "minutes_to_breach": float(target_minutes),
        "is_breached": False,
        "breached_at": None,
    }


def revenue_impact_from_summary(
    *,
    transaction_summary: dict | None,
    severity: Any,
) -> dict:
    """Build a `RevenueImpact` fragment from a frozen txn summary.

    `amount_at_risk_myr` is the order total. Severity-weighted confidence
    (high/critical → 80% expected-value, medium/low → 50%) gives a
    rough EV figure analysts can sort by. Ops-cost is a flat per-case
    constant tuned to Acme's typical investigation overhead.
    """
    sev = _severity_value(severity)
    amount = float((transaction_summary or {}).get("total_myr") or 0.0)
    confidence = 0.8 if sev in (Severity.HIGH.value, Severity.CRITICAL.value) else 0.5
    fp_ops_cost = 12.0  # MYR — analyst time
    return {
        "amount_at_risk_myr": round(amount, 2),
        "revenue_protected_if_correct_myr": round(amount * confidence, 2),
        "false_positive_ops_cost_myr": fp_ops_cost,
        "ev_resolution_value_myr": round(amount * confidence - fp_ops_cost, 2),
    }


def open_lifecycle_event(
    *,
    actor_id: str = "system",
    rule_types_fired: list[str] | None = None,
    note: str | None = None,
) -> dict:
    """Initial `LifecycleEvent` for status=open."""
    return {
        "at": _utcnow(),
        "from_status": None,
        "to_status": "open",
        "actor_type": "system",
        "actor_id": actor_id,
        "rule_types_fired": rule_types_fired or [],
        "note": note,
    }


def transition_lifecycle_event(
    *,
    from_status: str,
    to_status: str,
    actor_id: str,
    actor_type: str = "analyst",
    note: str | None = None,
) -> dict:
    """Subsequent `LifecycleEvent` for any status flip."""
    return {
        "at": _utcnow(),
        "from_status": from_status,
        "to_status": to_status,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "rule_types_fired": None,
        "note": note,
    }


def apply_sla_tick(*, sla: dict, opened_at: datetime, now: datetime | None = None) -> dict:
    """Recompute `elapsed_minutes` / `minutes_to_breach` / `is_breached`.

    Idempotent — once `is_breached=True` and `breached_at` is set, the
    value is preserved (we only ever set it the first time the deadline
    crosses).
    """
    now = now or _utcnow()
    target = sla.get("target_resolution_at")
    if target is None:
        return sla
    # Coerce naive datetimes (e.g. from the legacy seed path) to UTC so
    # subtraction with the tz-aware `now` doesn't raise. Naive timestamps
    # in this collection are always intended as UTC.
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    if isinstance(target, datetime) and target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (now - opened_at).total_seconds() / 60.0)
    remaining = (target - now).total_seconds() / 60.0
    breached = remaining <= 0
    out = dict(sla)
    out["elapsed_minutes"] = round(elapsed, 2)
    out["minutes_to_breach"] = round(remaining, 2)
    if breached and not sla.get("is_breached"):
        out["is_breached"] = True
        out["breached_at"] = now
    elif breached:
        out["is_breached"] = True
    return out
