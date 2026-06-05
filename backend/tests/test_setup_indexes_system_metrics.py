"""PR-11 — `system_metrics` TTL index registration.

Asserts the recorder's per-minute samples carry a 7-day TTL so the
collection auto-prunes — required because the BurstModeTile only ever
needs the last hour or two of samples and storage is finite.

No live Mongo: we read the module-level `REGULAR_INDEXES` table.
"""

from __future__ import annotations

from pymongo import ASCENDING

from app.config import settings
from app.core.constants import SYSTEM_METRICS
from scripts.setup_indexes import REGULAR_INDEXES


_SEVEN_DAYS_SECONDS = 7 * 24 * 3600


def test_system_metrics_ttl_registered() -> None:
    """`system_metrics` MUST register a TTL index on `recorded_at` with
    `expireAfterSeconds == 7 * 24 * 3600` so per-minute samples are
    auto-purged after a week."""
    entries = REGULAR_INDEXES.get(SYSTEM_METRICS)
    assert entries, f"REGULAR_INDEXES has no entry for {SYSTEM_METRICS!r}"

    ttl_specs = [
        (keys, opts)
        for keys, opts in entries
        if "expireAfterSeconds" in opts
    ]
    assert ttl_specs, (
        f"REGULAR_INDEXES[{SYSTEM_METRICS!r}] has no TTL entry "
        "(expected `expireAfterSeconds` on a `recorded_at` index)"
    )

    matching = [
        opts for keys, opts in ttl_specs
        if list(keys) == [("recorded_at", ASCENDING)]
    ]
    assert matching, (
        "TTL must be keyed off `recorded_at` ASC — that is the field the "
        "metrics_recorder writes."
    )

    opts = matching[0]
    assert opts["expireAfterSeconds"] == _SEVEN_DAYS_SECONDS, (
        f"TTL expireAfterSeconds should be {_SEVEN_DAYS_SECONDS} (7 days), "
        f"got {opts['expireAfterSeconds']}"
    )


def test_system_metrics_ttl_uses_settings() -> None:
    """The TTL must equal `settings.system_metrics_ttl_days * 24 * 3600`
    so a single config knob (env var) controls retention. This guards
    against a regression where the 7-day default is hard-coded."""
    entries = REGULAR_INDEXES.get(SYSTEM_METRICS, [])
    ttl_specs = [
        (keys, opts) for keys, opts in entries
        if "expireAfterSeconds" in opts
    ]
    assert ttl_specs, "no TTL index registered on system_metrics"
    expected = settings.system_metrics_ttl_days * 24 * 3600
    # At least one TTL entry must use the settings-derived value.
    matching = [opts for _keys, opts in ttl_specs
                if opts["expireAfterSeconds"] == expected]
    assert matching, (
        f"TTL must be derived from settings.system_metrics_ttl_days "
        f"(expected expireAfterSeconds={expected} for "
        f"{settings.system_metrics_ttl_days} days); got "
        f"{[o['expireAfterSeconds'] for _, o in ttl_specs]}"
    )


def test_system_metrics_ttl_on_recorded_at_field() -> None:
    """The TTL key MUST be on `recorded_at` — that's the field the
    metrics_recorder stamps on every sample, so it's the only field
    the server can use to age out old docs."""
    entries = REGULAR_INDEXES.get(SYSTEM_METRICS, [])
    ttl_specs = [
        (keys, opts) for keys, opts in entries
        if "expireAfterSeconds" in opts
    ]
    assert ttl_specs, "no TTL index registered on system_metrics"
    # TTL indexes must be single-field; the field must be `recorded_at`.
    for keys, _opts in ttl_specs:
        assert len(keys) == 1, (
            f"TTL index must be single-field, got keys={keys}"
        )
        field, _direction = keys[0]
        assert field == "recorded_at", (
            f"TTL key must be on `recorded_at`, got {field!r}"
        )


def test_system_metrics_burst_run_lookup_index_registered() -> None:
    """The dashboard scopes burst-window queries by `burst_run_id` then
    sorts by `recorded_at` desc — there MUST be a compound index that
    lets that query be index-only."""
    entries = REGULAR_INDEXES.get(SYSTEM_METRICS, [])
    keys_lists = [list(keys) for keys, _ in entries]
    assert [
        ("burst_run_id", ASCENDING),
        ("recorded_at", -1),
    ] in keys_lists, (
        "expected compound index (burst_run_id ASC, recorded_at DESC) "
        f"on {SYSTEM_METRICS!r}"
    )
