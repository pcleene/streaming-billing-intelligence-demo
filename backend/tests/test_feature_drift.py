"""KS-test + severity-band tests for the feature drift detector (Phase B.5).

Synthesises distributions for each severity band so the detector's
classification is deterministic.
"""

from __future__ import annotations

import random

from app.workers.feature_drift_detector import (
    SEVERITY_BANDS,
    _ks_two_sample,
    _severity_for,
    _stats,
)


def test_ks_identical_distributions_is_zero() -> None:
    rng = random.Random(0)
    s = [rng.gauss(50, 5) for _ in range(500)]
    d, p = _ks_two_sample(s, s)
    assert d == 0.0
    assert p == 1.0


def test_ks_shifted_distribution_is_alert() -> None:
    rng = random.Random(1)
    baseline = [rng.gauss(50, 5) for _ in range(1000)]
    # Shift mean by +20 → KS statistic should be well into 'alert'.
    drifted = [x + 20 for x in baseline]
    d, _ = _ks_two_sample(drifted, baseline)
    assert d >= 0.40
    assert _severity_for(d) == "alert"


def test_ks_modest_shift_is_warn_or_watch() -> None:
    rng = random.Random(2)
    baseline = [rng.gauss(50, 5) for _ in range(1000)]
    drifted = [x + 3 for x in baseline]
    d, _ = _ks_two_sample(drifted, baseline)
    assert _severity_for(d) in ("watch", "warn")


def test_severity_bands_sorted_descending() -> None:
    """alert > warn > watch — order matters for the cascading classifier."""
    thresholds = [t for _, t in SEVERITY_BANDS]
    assert thresholds == sorted(thresholds, reverse=True)


def test_severity_for_below_lowest_band_is_none() -> None:
    assert _severity_for(0.0) == "none"
    assert _severity_for(0.05) == "none"


def test_stats_handles_empty() -> None:
    s = _stats([])
    assert s["n"] == 0
    assert s["mean"] == 0.0


def test_stats_basic_quartiles() -> None:
    s = _stats([1.0, 2.0, 3.0, 4.0, 100.0])
    assert s["n"] == 5
    assert s["min"] == 1.0
    assert s["max"] == 100.0
    assert s["p50"] == 3.0
