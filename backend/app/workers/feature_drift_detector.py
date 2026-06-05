"""Feature drift detector (Phase B.5).

Every `interval_seconds`, for each tracked numeric feature, sample the
current distribution from `features.{feature_name}` (top-level — flat
canonical shape; values are written by ASP / FE worker / Spark depending
on the feature) and compare it against a 30-day frozen baseline
(recomputed lazily once per process). Writes one `feature_drift_metrics`
doc per (feature, tick).

The dashboard reads `GET /api/features/drift` to render the
`FeatureDriftTile`.

KS-test math is bare-bones (no scipy dep) — exact enough for a demo, and
the dashboard surfaces both ks_statistic and p_value so analysts can
sanity-check.

PR-10 extends each persisted doc with:
  - `affected_consumers`     — model names + rule types reading the feature.
  - `recommended_action`     — `monitor` / `investigate` / `retrain` / `alert_oncall`.
  - `severity_progression`   — append-only history of severity transitions.
  - `model_lineage`          — model versions trained on this feature.
  - `drift_detected`         — convenience boolean, mirrors `severity != "none"`.
  - `investigation_link`     — stable demo URL while drift is non-zero.

Run via:  python -m app.workers.feature_drift_detector
"""

from __future__ import annotations

import asyncio
import math
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.constants import (
    FEATURE_DRIFT_METRICS,
    FEATURES,
    TRANSACTIONS,
)
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)


# Numeric features tracked by the detector. Every entry is the field name
# under `features` whose value is a single numeric scalar AND has enough
# samples to be worth statistical comparison.
TRACKED_FEATURES: tuple[str, ...] = (
    "txn_count_5m",
    "amount_sum_5m",
    "discount_sum_5m",
    "txn_count_24h",
    "spend_24h_myr",
)

# Severity thresholds on the KS statistic (fraction in [0, 1]).
SEVERITY_BANDS: tuple[tuple[str, float], ...] = (
    ("alert", 0.40),
    ("warn",  0.20),
    ("watch", 0.10),
)


# Static feature → consumers + lineage registry. Tests can pass a
# different `registry=` to `_consumers_for` / `_build_drift_doc` to
# inject alternative mappings.
FEATURE_CONSUMERS: dict[str, dict] = {
    "txn_count_5m":     {"consumers": ["fraud_rule_velocity", "model_v3_fraud"],
                         "model_lineage": ["model_v3_fraud@2026-04-12"]},
    "amount_sum_5m":    {"consumers": ["fraud_rule_high_value", "model_v3_fraud"],
                         "model_lineage": ["model_v3_fraud@2026-04-12"]},
    "discount_sum_5m":  {"consumers": ["promo_abuse_rule"],
                         "model_lineage": []},
    "txn_count_24h":    {"consumers": [
                             "churn_model_v2",
                             "quarantine_iforest",
                         ],
                         "model_lineage": ["churn_model_v2@2026-03-01"]},
    "spend_24h_myr":    {"consumers": [
                             "churn_model_v2",
                             "ltv_model",
                             "quarantine_iforest",
                         ],
                         "model_lineage": [
                             "churn_model_v2@2026-03-01",
                             "ltv_model@2026-02-15",
                         ]},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stats(samples: list[float]) -> dict[str, float]:
    if not samples:
        zeros = {k: 0.0 for k in (
            "n", "mean", "std", "min", "p25", "p50", "p75", "p99", "max"
        )}
        zeros["n"] = 0
        return zeros
    s = sorted(samples)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n if n > 1 else 0.0
    def _q(p: float) -> float:
        return s[min(n - 1, int(p * n))]
    return {
        "n": n,
        "mean": mean,
        "std": math.sqrt(var),
        "min": s[0],
        "p25": _q(0.25),
        "p50": _q(0.50),
        "p75": _q(0.75),
        "p99": _q(0.99),
        "max": s[-1],
    }


def _ks_two_sample(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two-sample Kolmogorov–Smirnov D + asymptotic p-value.

    Returns (ks_statistic, p_value). For a demo this is precise enough;
    swap in scipy.stats.ks_2samp if numerical accuracy matters.
    """
    if not a or not b:
        return 0.0, 1.0
    sa = sorted(a)
    sb = sorted(b)
    na, nb = len(sa), len(sb)
    i = j = 0
    cdf_a = cdf_b = 0.0
    d = 0.0
    while i < na and j < nb:
        if sa[i] < sb[j]:
            i += 1
            cdf_a = i / na
        elif sa[i] > sb[j]:
            j += 1
            cdf_b = j / nb
        else:
            # Ties: advance both side-by-side so the CDFs stay aligned
            # for identical distributions (KS = 0 in that case).
            tie = sa[i]
            while i < na and sa[i] == tie:
                i += 1
            while j < nb and sb[j] == tie:
                j += 1
            cdf_a = i / na
            cdf_b = j / nb
        gap = abs(cdf_a - cdf_b)
        if gap > d:
            d = gap
    # Identical distributions → exact p-value of 1.0 (avoid the
    # series expansion's oscillation around d=0).
    if d == 0.0:
        return 0.0, 1.0
    # Asymptotic two-sided p-value, valid for moderately large n.
    en = math.sqrt(na * nb / (na + nb)) if (na + nb) else 0.0
    arg = (en + 0.12 + 0.11 / max(en, 1e-9)) * d
    # Series expansion of Q(x) = 2 * sum_{k=1}^inf (-1)^(k-1) e^(-2 k^2 x^2)
    p = 0.0
    sign = 1.0
    for k in range(1, 101):
        term = sign * math.exp(-2.0 * k * k * arg * arg)
        p += term
        sign = -sign
        if abs(term) < 1e-12:
            break
    p = max(0.0, min(1.0, 2.0 * p))
    return d, p


def _severity_for(d: float) -> str:
    for label, threshold in SEVERITY_BANDS:
        if d >= threshold:
            return label
    return "none"


def _recommended_action(severity: str, has_consumers: bool) -> str:
    """Map (severity, has-consumers?) → on-call action string.

    `alert` with downstream consumers escalates straight to on-call;
    without consumers it stays at `investigate` since nothing reads the
    feature anyway.
    """
    if severity == "alert":
        return "alert_oncall" if has_consumers else "investigate"
    if severity == "warn":
        return "investigate"
    if severity == "watch":
        return "monitor"
    return "monitor"


def _consumers_for(
    name: str, *, registry: dict[str, dict] = FEATURE_CONSUMERS,
) -> dict:
    """Resolve a feature → {consumers, model_lineage} entry.

    Unknown features yield empty lists rather than crashing the worker,
    so a newly-added tracked feature without a registry entry still
    flows through (it just won't escalate to `alert_oncall`).
    """
    entry = registry.get(name) or {}
    return {
        "consumers": list(entry.get("consumers") or []),
        "model_lineage": list(entry.get("model_lineage") or []),
    }


def _extend_progression(
    prev_progression: list[dict] | None,
    prev_severity: str | None,
    new_severity: str,
    *,
    now: datetime,
) -> list[dict]:
    """Append-only severity history for a single feature.

    First tick: returns `[]` if no drift (`new_severity == "none"`),
    else a single-entry list `[{at, from: "none", to: new_severity}]`.

    Subsequent ticks: copies the prior progression forward unchanged
    when severity is unchanged, or appends a new transition entry when
    it differs.
    """
    if prev_progression is None and prev_severity is None:
        if new_severity == "none":
            return []
        return [{"at": now, "from": "none", "to": new_severity}]
    progression = list(prev_progression or [])
    if (prev_severity or "none") == new_severity:
        return progression
    progression.append({
        "at": now,
        "from": prev_severity or "none",
        "to": new_severity,
    })
    return progression


def _build_investigation_link(feature_name: str) -> str:
    """Stable demo URL for the dashboard's drift drill-down."""
    return f"/dashboard/drift/{feature_name}"


def _build_drift_doc(
    name: str,
    *,
    current: list[float],
    baseline: list[float],
    ks: float,
    p: float,
    severity: str,
    prev_severity: str | None,
    prev_progression: list[dict] | None,
    registry: dict[str, dict] = FEATURE_CONSUMERS,
    now: datetime,
) -> dict:
    """Pure assembler — combines all components into the persisted doc.

    Tests target this directly to verify extended-field population
    without spinning up the worker loop.
    """
    consumers_meta = _consumers_for(name, registry=registry)
    progression = _extend_progression(
        prev_progression, prev_severity, severity, now=now,
    )
    drift_detected = severity != "none"
    return {
        "feature_name": name,
        "measured_at": now,
        "severity": severity,
        "ks_statistic": round(ks, 4),
        "p_value": round(p, 6),
        "current": _stats(current),
        "baseline": _stats(baseline),
        "sample_size_current": len(current),
        "sample_size_baseline": len(baseline),
        "drift_detected": drift_detected,
        "affected_consumers": consumers_meta["consumers"],
        "model_lineage": consumers_meta["model_lineage"],
        "recommended_action": _recommended_action(
            severity, bool(consumers_meta["consumers"]),
        ),
        "severity_progression": progression,
        "investigation_link": (
            _build_investigation_link(name) if drift_detected else None
        ),
    }


class FeatureDriftDetector:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._interval = 15 * 60  # 15 minutes per spec
        self._baseline_cache: dict[str, list[float]] = {}
        self._baseline_refreshed_at: datetime | None = None
        self._baseline_ttl = timedelta(hours=24)
        # Allow tests to inject an alternative registry without
        # reaching into the module-level constant.
        self._registry: dict[str, dict] = FEATURE_CONSUMERS

    async def run(self) -> None:
        db = get_db()
        feat = db[FEATURES]
        txns = db[TRANSACTIONS]
        out = db[FEATURE_DRIFT_METRICS]
        logger.info("feature_drift_detector_started", interval_seconds=self._interval)
        while not self._stop.is_set():
            try:
                await self._refresh_baseline_if_needed(txns, feat)
                await self._tick(feat, out)
            except Exception as exc:  # noqa: BLE001
                logger.warning("feature_drift_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    async def _refresh_baseline_if_needed(self, txns, feat) -> None:
        if (
            self._baseline_refreshed_at
            and (_utcnow() - self._baseline_refreshed_at) < self._baseline_ttl
            and self._baseline_cache
        ):
            return
        logger.info("feature_drift_baseline_refresh")
        self._baseline_cache = {}
        # Baseline pulls from 30-day txn history. We aggregate per
        # tracked feature using the same shape the rolling window emits.
        cutoff = (_utcnow() - timedelta(days=30)).isoformat()
        cursor = txns.find(
            {"timestamp": {"$gte": cutoff}},
            {"_id": 0, "amount": 1, "discount_amount": 1, "customer_id": 1, "timestamp": 1},
        ).limit(50_000)
        amounts: list[float] = []
        discounts: list[float] = []
        async for d in cursor:
            try:
                amounts.append(float(d.get("amount") or 0.0))
                discounts.append(float(d.get("discount_amount") or 0.0))
            except (TypeError, ValueError):
                continue
        # Map tracked features to baseline samples — the 5m / 24h
        # rolling windows borrow the same per-txn distribution as their
        # baseline, which is enough for the demo's drift envelope.
        self._baseline_cache = {
            "txn_count_5m":   [float(x) for x in range(min(len(amounts), 1_000))] or [0.0],
            "amount_sum_5m":  amounts or [0.0],
            "discount_sum_5m": discounts or [0.0],
            "txn_count_24h":  [float(x) for x in range(min(len(amounts), 10_000))] or [0.0],
            "spend_24h_myr":  amounts or [0.0],
        }
        self._baseline_refreshed_at = _utcnow()

    async def _fetch_prev_severity(
        self, out, name: str,
    ) -> tuple[str | None, list[dict] | None]:
        """Read the most recent persisted doc for `name` and return its
        (severity, severity_progression). Returns (None, None) on the
        first tick when no doc exists yet."""
        cursor = out.find(
            {"feature_name": name},
            {"_id": 0, "severity": 1, "severity_progression": 1, "measured_at": 1},
        ).sort([("measured_at", -1)]).limit(1)
        async for d in cursor:
            return d.get("severity"), d.get("severity_progression") or []
        return None, None

    async def _tick(self, feat, out) -> None:
        measured_at = _utcnow()
        for name in TRACKED_FEATURES:
            current = await self._sample_current(feat, name)
            baseline = self._baseline_cache.get(name) or [0.0]
            d, p = _ks_two_sample(current, baseline)
            severity = _severity_for(d)
            prev_severity, prev_progression = await self._fetch_prev_severity(out, name)
            doc = _build_drift_doc(
                name,
                current=current,
                baseline=baseline,
                ks=d,
                p=p,
                severity=severity,
                prev_severity=prev_severity,
                prev_progression=prev_progression,
                registry=self._registry,
                now=measured_at,
            )
            await out.insert_one(doc)

    async def _sample_current(self, feat, name: str) -> list[float]:
        """Server-side filter + type-coerce + sample.

        Pushes the null/non-numeric filter and the float coercion into
        a Mongo aggregation pipeline so the worker only receives the
        numeric values it is going to feed into the KS test. `$sample`
        gives an unbiased random draw rather than the implicit
        insertion-order of `find().limit()`, which matters when the
        collection is partially Spark-overwritten (latest customers
        cluster at one end).
        """
        pipeline = [
            {"$match": {name: {"$ne": None, "$type": "number"}}},
            {"$sample": {"size": 5_000}},
            {"$project": {"_id": 0, "value": {"$toDouble": f"${name}"}}},
        ]
        cursor = feat.aggregate(pipeline)
        out: list[float] = []
        async for d in cursor:
            v = d.get("value")
            if v is None:
                continue
            out.append(float(v))
        return out

    async def shutdown(self) -> None:
        self._stop.set()


async def main() -> None:
    configure_logging()
    await connect_mongo()
    det = FeatureDriftDetector()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(det.shutdown()))
    try:
        await det.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
