"""Seed `feature_drift_metrics` with a realistic time series (PR-14, revised).

Mirrors the per-tick shape that `app.workers.feature_drift_detector` writes
(see `_build_drift_doc`):

  - feature_name / measured_at / severity / drift_detected
  - ks_statistic / p_value / recommended_action
  - severity_progression  (entries shaped `{at, from, to}` to match runtime)
  - affected_consumers / model_lineage / investigation_link / analyst_actions
  - current / baseline    (per-tick distribution stats — `n,mean,std,p25,…`)

Each tracked feature gets its own KS trajectory across `history_days` of
ticks. The trajectory ramps from "none" toward the feature's *target*
severity over the second half of the window, so the dashboard chart shows
a believable build-up rather than a flat line. The latest tick per feature
is what `/api/features/.../drift-status` returns.

Run via:
    python -m scripts.seed_feature_drift_metrics
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.constants import FEATURE_DRIFT_METRICS
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.workers.feature_drift_detector import FEATURE_CONSUMERS, TRACKED_FEATURES

logger = get_logger(__name__)


SEED_MARKER = "pr14"
FEATURE_SET_VERSION = "v3.2.1"
CODE_VERSION = "1.0.0"

# Target severity per slot for the *latest* tick of each feature's trajectory.
SEVERITY_PLAN: tuple[str, ...] = ("none", "watch", "warn", "alert", "none", "none")

# One synthetic enrichment feature so the dashboard has variety beyond the
# 5 entries in TRACKED_FEATURES.
EXTRA_FEATURE = "txn_velocity_5m"

# Per-feature baseline distribution shape. Mean/std are MYR for amount-ish
# features and tx-counts for txn_count_*. These mirror what the runtime
# detector would compute over a 30-day rolling window.
BASELINE_PROFILES: dict[str, dict[str, float]] = {
    "txn_count_5m":     {"mean": 0.55,  "std": 0.80},
    "amount_sum_5m":    {"mean": 12.40, "std": 21.0},
    "discount_sum_5m":  {"mean": 1.45,  "std": 3.10},
    "txn_count_24h":    {"mean": 3.50,  "std": 2.10},
    "spend_24h_myr":    {"mean": 84.60, "std": 71.0},
    "txn_velocity_5m":  {"mean": 0.11,  "std": 0.15},
}

# KS bands match `app.workers.feature_drift_detector.SEVERITY_BANDS`.
_BAND_RANGES: dict[str, tuple[float, float]] = {
    "none":  (0.005, 0.045),
    "watch": (0.105, 0.195),  # leave a little headroom under warn
    "warn":  (0.205, 0.395),
    "alert": (0.405, 0.560),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _severity_for_ks(ks: float) -> str:
    """Map KS to severity using the runtime SEVERITY_BANDS thresholds."""
    if ks >= 0.40:
        return "alert"
    if ks >= 0.20:
        return "warn"
    if ks >= 0.10:
        return "watch"
    return "none"


def _recommended_action_for(severity: str, has_consumers: bool) -> str:
    """Match runtime `_recommended_action` semantics."""
    if severity == "alert":
        return "alert_oncall" if has_consumers else "investigate"
    if severity == "warn":
        return "investigate"
    return "monitor"


def _p_value_for_ks(ks: float, rng: random.Random) -> float:
    """Synthesise a plausible p-value: large-KS → near-zero, small-KS → ~uniform.

    We only need the rendered field to look right on the dashboard; the
    runtime detector computes a real KS p-value via the asymptotic series.
    """
    if ks >= 0.40:
        return round(rng.uniform(0.0, 0.005), 6)
    if ks >= 0.20:
        return round(rng.uniform(0.005, 0.04), 6)
    if ks >= 0.10:
        return round(rng.uniform(0.04, 0.15), 6)
    return round(rng.uniform(0.2, 1.0), 6)


def _stats_for(mean: float, std: float, *, n: int, rng: random.Random) -> dict:
    """Synthesise `_stats`-shaped fields without materialising n samples.

    The runtime detector emits `{n, mean, std, min, p25, p50, p75, p99, max}`
    by sorting the actual sample. For the seed we just want display-quality
    quantiles centred on `mean` with spread `std` — small jitter keeps each
    tick visually distinct on the dashboard.
    """
    jitter = rng.uniform(-0.03, 0.03)
    mean = max(0.0, mean * (1 + jitter))
    std = max(1e-6, std)
    return {
        "n": n,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(max(0.0, mean - 2.5 * std), 4),
        "p25": round(max(0.0, mean - 0.7 * std), 4),
        "p50": round(max(0.0, mean), 4),
        "p75": round(max(0.0, mean + 0.7 * std), 4),
        "p99": round(max(0.0, mean + 2.3 * std), 4),
        "max": round(max(0.0, mean + 3.0 * std), 4),
    }


def _affected_consumers(
    feature_name: str, *, now: datetime, rng: random.Random,
) -> list[dict]:
    """Pull entries from FEATURE_CONSUMERS, normalising to `{type, name, last_seen}`.

    Bare strings fall back to a `model` type unless the name carries a
    `fraud_rule_` / `promo_abuse_` prefix.
    """
    entry = FEATURE_CONSUMERS.get(feature_name) or {}
    raw = list(entry.get("consumers") or [])
    if not raw:
        return []
    selected = raw[: min(3, len(raw))]
    out: list[dict] = []
    for item in selected:
        if isinstance(item, dict):
            out.append(
                {
                    "type": item.get("type", "model"),
                    "name": item.get("name", "unknown"),
                    "last_seen": item.get("last_seen") or now,
                }
            )
            continue
        name = str(item)
        ctype = "rule" if name.startswith(("fraud_rule_", "promo_abuse_")) else "model"
        offset = rng.randint(60, 3600)
        out.append(
            {"type": ctype, "name": name, "last_seen": now - timedelta(seconds=offset)}
        )
    return out


def _smooth(values: list[float], *, window: int = 5) -> list[float]:
    """Simple centred moving average to damp tick-level noise.

    Keeps the trajectory's overall shape but stops the severity from
    flickering across band boundaries when the raw value sits right on a
    threshold. `window=5` over 30-minute ticks ≈ 2.5h smoothing, which
    matches what a real KS detector with a 30-day baseline behaves like.

    The window is auto-scaled down for tiny trajectories (e.g. test
    fixtures with <12 ticks) so we don't pull the final ticks back below
    their target band.
    """
    n = len(values)
    if window <= 1 or n <= 2:
        return list(values)
    eff = min(window, max(1, n // 4))
    if eff <= 1:
        return list(values)
    half = eff // 2
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _ks_trajectory(
    target_severity: str,
    *,
    n_ticks: int,
    rng: random.Random,
) -> list[float]:
    """Build a per-tick KS sequence that lands on `target_severity`.

    Strategy:
      - "none"  → noisy hover inside the none band the whole time.
      - "watch" → none-band for the first 70%, then a smooth ramp into watch.
      - "warn"  → none → watch → warn over the last ~50% of the window.
      - "alert" → none → watch → warn → alert over the last ~60%.

    The ramp uses a smoothstep so the chart rises gradually rather than as
    a step. Per-tick noise (~±0.015) keeps the line from looking synthetic.
    """
    def _noise() -> float:
        return rng.uniform(-0.012, 0.012)

    out: list[float] = []
    if target_severity == "none":
        for _ in range(n_ticks):
            lo, hi = _BAND_RANGES["none"]
            out.append(max(0.0, rng.uniform(lo, hi) + _noise() * 0.4))
        return out

    if target_severity == "watch":
        ramp_start = int(n_ticks * 0.70)
        target = rng.uniform(*_BAND_RANGES["watch"])
        for i in range(n_ticks):
            if i < ramp_start:
                lo, hi = _BAND_RANGES["none"]
                out.append(max(0.0, rng.uniform(lo, hi) + _noise()))
            else:
                t = (i - ramp_start) / max(1, n_ticks - 1 - ramp_start)
                s = t * t * (3 - 2 * t)  # smoothstep
                base = _BAND_RANGES["none"][1] + (target - _BAND_RANGES["none"][1]) * s
                out.append(max(0.0, base + _noise()))
        return _smooth(out, window=5)

    if target_severity == "warn":
        ramp_start = int(n_ticks * 0.55)
        target = rng.uniform(*_BAND_RANGES["warn"])
        for i in range(n_ticks):
            if i < ramp_start:
                lo, hi = _BAND_RANGES["none"]
                out.append(max(0.0, rng.uniform(lo, hi) + _noise()))
            else:
                t = (i - ramp_start) / max(1, n_ticks - 1 - ramp_start)
                s = t * t * (3 - 2 * t)
                base = _BAND_RANGES["none"][1] + (target - _BAND_RANGES["none"][1]) * s
                out.append(max(0.0, base + _noise()))
        return _smooth(out, window=5)

    # alert
    ramp_start = int(n_ticks * 0.40)
    target = rng.uniform(*_BAND_RANGES["alert"])
    for i in range(n_ticks):
        if i < ramp_start:
            lo, hi = _BAND_RANGES["none"]
            out.append(max(0.0, rng.uniform(lo, hi) + _noise()))
        else:
            t = (i - ramp_start) / max(1, n_ticks - 1 - ramp_start)
            s = t * t * (3 - 2 * t)
            base = _BAND_RANGES["none"][1] + (target - _BAND_RANGES["none"][1]) * s
            out.append(max(0.0, base + _noise()))
    # Smooth the alert trajectory through 5-tick moving average so the
    # severity timeline doesn't flicker on threshold crossings.
    return _smooth(out, window=5)


def _slot_features() -> list[str]:
    """Per-slot feature name; pads TRACKED_FEATURES with the synthetic extra."""
    return list(TRACKED_FEATURES) + [EXTRA_FEATURE]


def _build_tick_doc(
    feature_name: str,
    *,
    measured_at: datetime,
    ks: float,
    severity: str,
    progression: list[dict],
    rng: random.Random,
) -> dict[str, Any]:
    """Assemble one drift_metrics doc for a single tick."""
    profile = BASELINE_PROFILES.get(feature_name, {"mean": 1.0, "std": 1.0})
    base_mean = profile["mean"]
    base_std = profile["std"]
    # Current mean shifts with KS magnitude — high KS == big distribution change.
    shift_factor = 1.0 + ks * 2.5
    current_mean = base_mean * shift_factor
    current_std = base_std * (1.0 + ks * 0.6)

    consumers = _affected_consumers(feature_name, now=measured_at, rng=rng)
    return {
        "feature_name": feature_name,
        "measured_at": measured_at,
        "severity": severity,
        "ks_statistic": round(ks, 4),
        "p_value": _p_value_for_ks(ks, rng),
        "recommended_action": _recommended_action_for(severity, bool(consumers)),
        "severity_progression": progression,
        "affected_consumers": consumers,
        "drift_detected": severity != "none",
        "model_lineage": {
            "feature_set_version": FEATURE_SET_VERSION,
            "code_version": CODE_VERSION,
            "models": list(
                (FEATURE_CONSUMERS.get(feature_name) or {}).get("model_lineage") or []
            ),
        },
        "current": _stats_for(current_mean, current_std, n=240, rng=rng),
        "baseline": _stats_for(base_mean, base_std, n=12_500, rng=rng),
        "sample_size_current": 240,
        "sample_size_baseline": 12_500,
        "investigation_link": f"/features/{feature_name}",
        "analyst_actions": [],
        "_seed_marker": SEED_MARKER,
    }


def _build_feature_history(
    feature_name: str,
    target_severity: str,
    *,
    now: datetime,
    history_days: int,
    tick_interval_minutes: int,
    rng: random.Random,
) -> list[dict]:
    """Generate the full per-tick time series for one feature.

    Severity progression accumulates across ticks: each transition appends
    one `{at, from, to}` entry, mirroring the runtime detector's
    `_extend_progression`.
    """
    n_ticks = max(1, (history_days * 24 * 60) // tick_interval_minutes)
    trajectory = _ks_trajectory(target_severity, n_ticks=n_ticks, rng=rng)
    docs: list[dict] = []
    progression: list[dict] = []
    prev_severity: str | None = None
    for tick_idx, ks in enumerate(trajectory):
        # Oldest tick first → newest tick is the "current" state.
        ago_min = (n_ticks - 1 - tick_idx) * tick_interval_minutes
        measured_at = now - timedelta(minutes=ago_min)
        severity = _severity_for_ks(ks)
        if prev_severity is None:
            if severity != "none":
                progression = [{"at": measured_at, "from": "none", "to": severity}]
        elif prev_severity != severity:
            progression = progression + [
                {"at": measured_at, "from": prev_severity, "to": severity}
            ]
        prev_severity = severity
        docs.append(
            _build_tick_doc(
                feature_name,
                measured_at=measured_at,
                ks=ks,
                severity=severity,
                progression=list(progression),
                rng=rng,
            )
        )
    return docs


async def _delete_marker(coll, marker: str) -> None:
    """Idempotent wipe; tolerates absence of `delete_many` on FakeDB."""
    delete_many = getattr(coll, "delete_many", None)
    if callable(delete_many):
        try:
            await delete_many({"_seed_marker": marker})
            return
        except NotImplementedError:
            pass
    docs = []
    async for d in coll.find({"_seed_marker": marker}):
        docs.append(d)
    for _ in docs:
        await coll.delete_one({"_seed_marker": marker})


async def _insert_docs(coll, docs: list[dict]) -> None:
    insert_many = getattr(coll, "insert_many", None)
    if callable(insert_many) and docs:
        try:
            await insert_many(docs, ordered=False)
            return
        except NotImplementedError:
            pass
    for d in docs:
        await coll.insert_one(d)


async def main(
    db,
    *,
    history_days: int = 14,
    tick_interval_minutes: int = 30,
) -> dict:
    """Seed per-feature drift time series.

    Returns:
        {
            "inserted": <total tick docs>,
            "features": <feature count>,
            "ticks_per_feature": <n_ticks>,
            "by_severity": <count of latest ticks landing in each band>,
        }
    """
    rng = random.Random(29)
    coll = db[FEATURE_DRIFT_METRICS]

    await _delete_marker(coll, SEED_MARKER)

    now = _utcnow()
    features = _slot_features()
    n_slots = min(len(features), len(SEVERITY_PLAN))
    n_ticks = max(1, (history_days * 24 * 60) // tick_interval_minutes)
    all_docs: list[dict] = []
    by_severity: dict[str, int] = {"none": 0, "watch": 0, "warn": 0, "alert": 0}

    for slot in range(n_slots):
        feature_name = features[slot]
        target_severity = SEVERITY_PLAN[slot]
        feature_docs = _build_feature_history(
            feature_name,
            target_severity,
            now=now,
            history_days=history_days,
            tick_interval_minutes=tick_interval_minutes,
            rng=rng,
        )
        all_docs.extend(feature_docs)
        # Histogram is over the *latest* tick per feature — that's what the
        # dashboard surfaces, and it should match SEVERITY_PLAN.
        latest_severity = feature_docs[-1]["severity"]
        by_severity[latest_severity] = by_severity.get(latest_severity, 0) + 1

    await _insert_docs(coll, all_docs)

    result = {
        "inserted": len(all_docs),
        "features": n_slots,
        "ticks_per_feature": n_ticks,
        "by_severity": by_severity,
    }
    logger.info("seed_feature_drift_metrics_done", **result)
    return result


async def _entrypoint() -> None:
    configure_logging()
    await connect_mongo()
    try:
        await main(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_entrypoint())
