"""Unit tests for `scripts/seed_feature_drift_metrics.py` (PR-14, revised).

The seed now emits a per-tick *time series* per feature (not a single
snapshot per feature) so the drift detail page chart has data and the
runtime/seed schemas match. We pin tests to a tiny ticks-per-feature
count via the `history_days` / `tick_interval_minutes` knobs to keep
runtime fast while still exercising the time-series code path.
"""

from __future__ import annotations

import pytest

from app.core.constants import FEATURE_DRIFT_METRICS
from scripts.seed_feature_drift_metrics import (
    SEED_MARKER,
    SEVERITY_PLAN,
    main as seed_drift_main,
)
from tests._fakes import FakeDB


# Tiny fixture: 1 day @ 240 min ticks → 6 ticks/feature → 36 docs total.
_HISTORY_DAYS = 1
_TICK_MINUTES = 240
_EXPECTED_TICKS_PER_FEATURE = (_HISTORY_DAYS * 24 * 60) // _TICK_MINUTES
_EXPECTED_FEATURES = len(SEVERITY_PLAN)
_EXPECTED_TOTAL_DOCS = _EXPECTED_FEATURES * _EXPECTED_TICKS_PER_FEATURE


async def _seed(db: FakeDB) -> dict:
    return await seed_drift_main(
        db,
        history_days=_HISTORY_DAYS,
        tick_interval_minutes=_TICK_MINUTES,
    )


def _latest_per_feature(docs: list[dict]) -> dict[str, dict]:
    """Pick the doc with the largest `measured_at` per feature_name."""
    out: dict[str, dict] = {}
    for d in docs:
        name = d["feature_name"]
        if name not in out or d["measured_at"] > out[name]["measured_at"]:
            out[name] = d
    return out


@pytest.mark.asyncio
async def test_seed_emits_full_time_series() -> None:
    db = FakeDB()
    result = await _seed(db)

    assert result["features"] == _EXPECTED_FEATURES
    assert result["ticks_per_feature"] == _EXPECTED_TICKS_PER_FEATURE
    assert result["inserted"] == _EXPECTED_TOTAL_DOCS
    docs = db[FEATURE_DRIFT_METRICS]._docs
    assert len(docs) == _EXPECTED_TOTAL_DOCS
    assert all(d.get("_seed_marker") == SEED_MARKER for d in docs)


@pytest.mark.asyncio
async def test_seed_is_idempotent() -> None:
    db = FakeDB()
    first = await _seed(db)
    second = await _seed(db)

    assert first["inserted"] == second["inserted"]
    docs = db[FEATURE_DRIFT_METRICS]._docs
    # Marker wipe → exactly one full pass survives.
    assert len(docs) == _EXPECTED_TOTAL_DOCS


@pytest.mark.asyncio
async def test_seed_latest_tick_severity_matches_plan() -> None:
    """The *latest* tick per feature should land on the planned severity."""
    db = FakeDB()
    result = await _seed(db)

    # SEVERITY_PLAN = ("none", "watch", "warn", "alert", "none", "none")
    # → 3 none, 1 watch, 1 warn, 1 alert at the latest tick.
    by_sev = result["by_severity"]
    assert by_sev["none"] == 3
    assert by_sev["watch"] == 1
    assert by_sev["warn"] == 1
    assert by_sev["alert"] == 1

    latest = _latest_per_feature(db[FEATURE_DRIFT_METRICS]._docs)
    histogram: dict[str, int] = {"none": 0, "watch": 0, "warn": 0, "alert": 0}
    for d in latest.values():
        histogram[d["severity"]] = histogram.get(d["severity"], 0) + 1
    assert histogram == by_sev


@pytest.mark.asyncio
async def test_seed_drift_detected_mirrors_severity() -> None:
    db = FakeDB()
    await _seed(db)
    for d in db[FEATURE_DRIFT_METRICS]._docs:
        sev = d["severity"]
        assert d["drift_detected"] is (sev != "none")


@pytest.mark.asyncio
async def test_seed_progression_uses_runtime_shape() -> None:
    """Severity progression entries must carry `{at, from, to}` to match
    the runtime detector — the FE renders `{step.from} → {step.to}`."""
    db = FakeDB()
    await _seed(db)
    latest = _latest_per_feature(db[FEATURE_DRIFT_METRICS]._docs)
    for d in latest.values():
        if d["severity"] == "none":
            assert d["severity_progression"] == []
            continue
        assert d["severity_progression"], (
            f"{d['feature_name']}@{d['severity']} should have progression"
        )
        for step in d["severity_progression"]:
            assert "at" in step
            assert "from" in step
            assert "to" in step


@pytest.mark.asyncio
async def test_seed_recommended_action_mapping() -> None:
    """Per-severity recommended_action mapping mirrors runtime semantics."""
    db = FakeDB()
    await _seed(db)
    expected = {
        "none": {"monitor"},
        "watch": {"monitor"},
        "warn": {"investigate"},
        # `alert` → `alert_oncall` only when the registry has consumers,
        # else `investigate`. All planned-alert features in this seed
        # (txn_count_24h) have model consumers, so we expect alert_oncall.
        "alert": {"alert_oncall", "investigate"},
    }
    for d in db[FEATURE_DRIFT_METRICS]._docs:
        sev = d["severity"]
        assert d["recommended_action"] in expected[sev], (
            f"{sev} → {d['recommended_action']} not in {expected[sev]}"
        )


@pytest.mark.asyncio
async def test_seed_affected_consumers_when_in_registry() -> None:
    """If a feature is in FEATURE_CONSUMERS, every tick should carry
    `affected_consumers` shaped as `{type, name, last_seen}`."""
    from app.workers.feature_drift_detector import FEATURE_CONSUMERS

    db = FakeDB()
    await _seed(db)
    for d in db[FEATURE_DRIFT_METRICS]._docs:
        name = d["feature_name"]
        if name in FEATURE_CONSUMERS:
            consumers = d["affected_consumers"]
            assert consumers, f"expected consumers for {name}"
            for c in consumers:
                assert "type" in c and c["type"] in {"rule", "model"}
                assert "name" in c and isinstance(c["name"], str)
                assert "last_seen" in c


@pytest.mark.asyncio
async def test_seed_stats_blocks_present() -> None:
    """Each tick should carry `current` and `baseline` distribution
    blocks shaped like the runtime detector's `_stats` output."""
    db = FakeDB()
    await _seed(db)
    expected_keys = {"n", "mean", "std", "min", "p25", "p50", "p75", "p99", "max"}
    for d in db[FEATURE_DRIFT_METRICS]._docs:
        for key in ("current", "baseline"):
            block = d.get(key)
            assert isinstance(block, dict), f"{key} missing on {d['feature_name']}"
            assert expected_keys.issubset(block.keys()), (
                f"{key} on {d['feature_name']} missing keys: "
                f"{expected_keys - set(block.keys())}"
            )


@pytest.mark.asyncio
async def test_seed_ks_statistic_landing_band() -> None:
    """The latest tick's KS statistic should sit inside its target band."""
    db = FakeDB()
    await _seed(db)
    latest = _latest_per_feature(db[FEATURE_DRIFT_METRICS]._docs)
    for d in latest.values():
        ks = float(d["ks_statistic"])
        sev = d["severity"]
        if sev == "alert":
            assert ks >= 0.40
        elif sev == "warn":
            assert 0.20 <= ks < 0.40
        elif sev == "watch":
            assert 0.10 <= ks < 0.20
        else:
            assert ks < 0.10


@pytest.mark.asyncio
async def test_seed_model_lineage_and_link() -> None:
    db = FakeDB()
    await _seed(db)
    for d in db[FEATURE_DRIFT_METRICS]._docs:
        ml = d["model_lineage"]
        assert isinstance(ml, dict)
        assert ml["feature_set_version"] == "v3.2.1"
        assert ml["code_version"] == "1.0.0"
        # New: the seed now also surfaces the registry's model lineage list
        # under `model_lineage.models` so downstream UIs can show the
        # actual models trained on this feature.
        assert "models" in ml and isinstance(ml["models"], list)
        assert d["investigation_link"] == f"/features/{d['feature_name']}"
        assert d["analyst_actions"] == []
