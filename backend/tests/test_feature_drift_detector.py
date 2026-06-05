"""Unit + integration tests for the PR-10 extensions of
`app.workers.feature_drift_detector`.

These exercise the pure helpers (`_extend_progression`,
`_recommended_action`, `_consumers_for`, `_build_drift_doc`) as well as
the FakeDB-backed integration path that proves the
`severity_progression` array grows monotonically across three ticks
spanning watch → warn → alert.

Note: PR-1 KS / severity / stats math is covered by
`tests/test_feature_drift.py`. This module focuses purely on the
extended-fields populated in PR-10.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.constants import FEATURE_DRIFT_METRICS, FEATURES
from app.workers import feature_drift_detector as fdd
from app.workers.feature_drift_detector import (
    FEATURE_CONSUMERS,
    FeatureDriftDetector,
    _build_drift_doc,
    _consumers_for,
    _extend_progression,
    _recommended_action,
)
from tests._fakes import FakeDB


NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# _extend_progression
# ----------------------------------------------------------------------


def test_extend_progression_first_tick_no_drift() -> None:
    out = _extend_progression(None, None, "none", now=NOW)
    assert out == []


def test_extend_progression_first_tick_with_drift() -> None:
    out = _extend_progression(None, None, "watch", now=NOW)
    assert len(out) == 1
    assert out[0]["from"] == "none"
    assert out[0]["to"] == "watch"
    assert out[0]["at"] == NOW


def test_extend_progression_no_change() -> None:
    seed = [{"at": NOW, "from": "none", "to": "watch"}]
    out = _extend_progression(seed, "watch", "watch", now=NOW + timedelta(minutes=15))
    assert len(out) == 1
    assert out == seed


def test_extend_progression_escalation() -> None:
    t0 = NOW
    t1 = NOW + timedelta(minutes=15)
    t2 = NOW + timedelta(minutes=30)
    p1 = _extend_progression(None, None, "watch", now=t0)
    p2 = _extend_progression(p1, "watch", "warn", now=t1)
    p3 = _extend_progression(p2, "warn", "alert", now=t2)
    assert len(p3) == 3
    transitions = [(e["from"], e["to"]) for e in p3]
    assert transitions == [
        ("none", "watch"),
        ("watch", "warn"),
        ("warn", "alert"),
    ]


# ----------------------------------------------------------------------
# _recommended_action
# ----------------------------------------------------------------------


def test_recommended_action_alert_with_consumers() -> None:
    assert _recommended_action("alert", True) == "alert_oncall"


def test_recommended_action_alert_without_consumers() -> None:
    assert _recommended_action("alert", False) == "investigate"


def test_recommended_action_warn_returns_investigate() -> None:
    assert _recommended_action("warn", True) == "investigate"
    assert _recommended_action("warn", False) == "investigate"


def test_recommended_action_watch_returns_monitor() -> None:
    assert _recommended_action("watch", True) == "monitor"


def test_recommended_action_none_returns_monitor() -> None:
    assert _recommended_action("none", True) == "monitor"


# ----------------------------------------------------------------------
# _consumers_for
# ----------------------------------------------------------------------


def test_consumers_for_known_feature() -> None:
    out = _consumers_for("txn_count_5m")
    assert "fraud_rule_velocity" in out["consumers"]
    assert "model_v3_fraud" in out["consumers"]
    assert out["model_lineage"] == ["model_v3_fraud@2026-04-12"]


def test_consumers_for_unknown_feature() -> None:
    out = _consumers_for("unknown_feature_xyz")
    assert out["consumers"] == []
    assert out["model_lineage"] == []


def test_consumers_for_uses_injected_registry() -> None:
    registry = {
        "feat_a": {"consumers": ["custom_rule"], "model_lineage": ["m@1"]},
    }
    out = _consumers_for("feat_a", registry=registry)
    assert out["consumers"] == ["custom_rule"]
    assert out["model_lineage"] == ["m@1"]
    # And the static registry is untouched.
    assert "feat_a" not in FEATURE_CONSUMERS


# ----------------------------------------------------------------------
# _build_drift_doc
# ----------------------------------------------------------------------


def test_build_drift_doc_populates_extended_fields() -> None:
    doc = _build_drift_doc(
        "txn_count_5m",
        current=[1.0, 2.0, 3.0, 4.0],
        baseline=[10.0, 11.0, 12.0, 13.0],
        ks=0.45,
        p=0.001,
        severity="alert",
        prev_severity="warn",
        prev_progression=[
            {"at": NOW - timedelta(minutes=15), "from": "none", "to": "warn"}
        ],
        now=NOW,
    )
    assert doc["feature_name"] == "txn_count_5m"
    assert doc["severity"] == "alert"
    assert doc["drift_detected"] is True
    assert doc["affected_consumers"] == [
        "fraud_rule_velocity", "model_v3_fraud",
    ]
    assert doc["model_lineage"] == ["model_v3_fraud@2026-04-12"]
    assert doc["recommended_action"] == "alert_oncall"
    # Progression appended a warn → alert transition.
    assert len(doc["severity_progression"]) == 2
    assert doc["severity_progression"][-1]["from"] == "warn"
    assert doc["severity_progression"][-1]["to"] == "alert"
    assert doc["investigation_link"] == "/dashboard/drift/txn_count_5m"
    # Standard distribution math passed through.
    assert doc["sample_size_current"] == 4
    assert doc["sample_size_baseline"] == 4
    assert doc["ks_statistic"] == 0.45


def test_drift_detected_false_when_no_drift() -> None:
    doc = _build_drift_doc(
        "txn_count_5m",
        current=[1.0, 2.0, 3.0],
        baseline=[1.0, 2.0, 3.0],
        ks=0.0,
        p=1.0,
        severity="none",
        prev_severity=None,
        prev_progression=None,
        now=NOW,
    )
    assert doc["severity"] == "none"
    assert doc["drift_detected"] is False
    assert doc["recommended_action"] == "monitor"
    assert doc["severity_progression"] == []
    assert doc["investigation_link"] is None


# ----------------------------------------------------------------------
# Integration — three-tick monotonic escalation against FakeDB
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_severity_progression_monotonic_over_three_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §B.5 gate — `severity escalates monotonically over three runs
    (watch → warn → alert)`. We feed `_ks_two_sample` predetermined KS
    values that map onto the three severity bands, run three `_tick`s,
    and assert the third doc carries a 3-entry `severity_progression`."""
    db = FakeDB()
    feat = db[FEATURES]
    out = db[FEATURE_DRIFT_METRICS]

    det = FeatureDriftDetector()
    # Seed baseline in-memory so `_tick` skips the heavy refresh path.
    det._baseline_cache = {name: [0.0] for name in fdd.TRACKED_FEATURES}
    det._baseline_refreshed_at = NOW

    # Pin time so each tick produces a strictly increasing measured_at.
    timeline = iter([
        NOW,
        NOW + timedelta(minutes=15),
        NOW + timedelta(minutes=30),
    ])

    def _now_stub() -> datetime:
        return next(timeline)

    monkeypatch.setattr(fdd, "_utcnow", _now_stub)

    # Sampling: returns small dummy current. The KS math will be
    # short-circuited by our `_ks_two_sample` stub, so the values here
    # don't drive the severity decision.
    async def _stub_sample(self_, _coll, _name):  # noqa: ARG001
        return [1.0, 2.0, 3.0]

    monkeypatch.setattr(
        FeatureDriftDetector, "_sample_current", _stub_sample,
    )

    # Stub KS — returns watch (0.12), warn (0.25), alert (0.45) per
    # tick. Because each tick iterates over all 5 tracked features, we
    # need the same value to apply for every feature in that tick.
    tick_values = iter([0.12, 0.25, 0.45])
    current_d: dict[str, float] = {"d": 0.0}

    def _ks_stub(a, b):  # noqa: ARG001
        return current_d["d"], 0.001

    monkeypatch.setattr(fdd, "_ks_two_sample", _ks_stub)

    for d_value in tick_values:
        current_d["d"] = d_value
        await det._tick(feat, out)

    # All TRACKED_FEATURES received three ticks; pick one and assert
    # the persisted progression climbs through the three severities.
    target = "txn_count_5m"
    docs = [d for d in out._docs if d["feature_name"] == target]
    assert len(docs) == 3
    docs.sort(key=lambda d: d["measured_at"])
    third = docs[-1]
    assert third["severity"] == "alert"
    assert third["drift_detected"] is True
    assert third["recommended_action"] == "alert_oncall"
    progression = third["severity_progression"]
    assert len(progression) == 3
    transitions = [(e["from"], e["to"]) for e in progression]
    assert transitions == [
        ("none", "watch"),
        ("watch", "warn"),
        ("warn", "alert"),
    ]


@pytest.mark.asyncio
async def test_tick_first_run_with_no_drift_writes_empty_progression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean tick where every feature is at KS=0 should persist a doc
    with severity=none, drift_detected=False, and an empty progression
    list — i.e. the very first time we see a feature, "no drift" is not
    a transition."""
    db = FakeDB()
    feat = db[FEATURES]
    out = db[FEATURE_DRIFT_METRICS]

    det = FeatureDriftDetector()
    det._baseline_cache = {name: [0.0] for name in fdd.TRACKED_FEATURES}
    det._baseline_refreshed_at = NOW

    monkeypatch.setattr(fdd, "_utcnow", lambda: NOW)

    async def _stub_sample(self_, _coll, _name):  # noqa: ARG001
        return [0.0]

    monkeypatch.setattr(
        FeatureDriftDetector, "_sample_current", _stub_sample,
    )
    monkeypatch.setattr(fdd, "_ks_two_sample", lambda a, b: (0.0, 1.0))  # noqa: ARG005

    await det._tick(feat, out)

    for name in fdd.TRACKED_FEATURES:
        doc = next(d for d in out._docs if d["feature_name"] == name)
        assert doc["severity"] == "none"
        assert doc["drift_detected"] is False
        assert doc["severity_progression"] == []
        assert doc["investigation_link"] is None
        assert doc["recommended_action"] == "monitor"
