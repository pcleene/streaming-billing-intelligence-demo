"""Seed `system_metrics` with one burst run + a steady baseline (PR-14).

Produces deterministic, dashboard-friendly fixture data so the
BurstModeTile and the `get_burst_status` envelope have something
plausible to render on a fresh database.

Two windows are written per invocation:

  * **Burst run** — `samples_burst` rows tagged `mode="burst"` and
    sharing a single, day-deterministic `burst_run_id` of the form
    `seed_burst_<YYYY-MM-DD>`. The synthesized profile mirrors the
    PR-11 burst spec: a 50→260 TPS ramp-up, a peak hold around
    220–300 TPS, and a 260→100 ramp-down. Latencies inflate during
    the peak and settle back afterwards.
  * **Steady baseline** — `samples_steady` rows tagged `mode="steady"`
    written *before* the burst window so the timeline stays causal:
    quiet 8–25 TPS with 60–110 ms p99.

Idempotent: running `main(db)` twice yields the same final state. We
delete any prior PR-14 seed rows up front (matched by either the
`seed_burst_*` `burst_run_id` namespace or the `_seed_marker == "pr14"`
tag) before re-inserting.

Run via:
    python -m scripts.seed_system_metrics
"""

from __future__ import annotations

import argparse
import asyncio
import random
from datetime import date, datetime, timedelta, timezone

from app.core.constants import SYSTEM_METRICS
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)


_SEED_MARKER = "pr14"
_SEED_BURST_PREFIX = "seed_burst_"
_INTERVAL_SECONDS = 30
_STEADY_GAP_SECONDS = 60  # 1 min gap between steady end and burst start


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _burst_tps(i: int, total: int, rng: random.Random) -> float:
    """Return synthetic observed_tps for the i-th burst sample.

    Mirrors the PR-11 profile: ramp-up over the first 10 samples,
    plateau in the middle, ramp-down across the tail.
    """
    ramp_up = max(1, min(10, total // 6))
    ramp_down = max(1, min(20, total // 3))
    plateau_end = total - ramp_down
    if i < ramp_up:
        # 50 → 260 over ramp-up
        base = 50.0 + (260.0 - 50.0) * (i / max(1, ramp_up - 1)) if ramp_up > 1 else 260.0
        jitter = rng.uniform(-5.0, 5.0)
    elif i < plateau_end:
        # Hold 220-300
        base = rng.uniform(220.0, 300.0)
        jitter = 0.0
    else:
        # 260 → 100 over ramp-down
        k = i - plateau_end
        n = max(1, total - plateau_end - 1)
        base = 260.0 - (260.0 - 100.0) * (k / n) if n > 0 else 100.0
        jitter = rng.uniform(-6.0, 6.0)
    return max(0.0, round(base + jitter, 2))


def _burst_p99(i: int, total: int, rng: random.Random) -> float:
    """Latency profile that tracks the burst phases."""
    ramp_up = max(1, min(10, total // 6))
    ramp_down = max(1, min(20, total // 3))
    plateau_end = total - ramp_down
    if i < ramp_up:
        return round(rng.uniform(80.0, 180.0), 1)
    if i < plateau_end:
        return round(rng.uniform(200.0, 340.0), 1)
    return round(rng.uniform(90.0, 150.0), 1)


def _build_burst_doc(
    *,
    i: int,
    total: int,
    anchor: datetime,
    burst_run_id: str,
    rng: random.Random,
) -> dict:
    # Stretch backwards from anchor at 30s intervals.
    # i=0 is oldest, i=total-1 is newest.
    offset_seconds = (total - 1 - i) * _INTERVAL_SECONDS
    recorded_at = anchor - timedelta(seconds=offset_seconds)

    observed_tps = _burst_tps(i, total, rng)
    p99 = _burst_p99(i, total, rng)
    p50 = round(p99 * 0.35 + rng.uniform(-3.0, 3.0), 1)
    p50 = max(1.0, min(p50, p99))  # ensure p50 <= p99 and positive
    rule_eval_p99 = round(p99 * 0.7 + rng.uniform(-4.0, 4.0), 1)
    rule_eval_p99 = max(1.0, rule_eval_p99)
    quarantine_per_sec = round(observed_tps * 0.05 + rng.uniform(-0.4, 0.4), 2)
    quarantine_per_sec = max(0.0, quarantine_per_sec)
    txns_in_window = int(round(observed_tps * _INTERVAL_SECONDS))
    cases_in_window = int(round(quarantine_per_sec * _INTERVAL_SECONDS))

    return {
        "recorded_at": recorded_at,
        "mode": "burst",
        "burst_run_id": burst_run_id,
        "observed_tps": observed_tps,
        "p50_ms_ingest": p50,
        "p99_ms_ingest": p99,
        "rule_eval_p99_ms": rule_eval_p99,
        "quarantine_per_sec": quarantine_per_sec,
        "txns_in_window": txns_in_window,
        "cases_in_window": cases_in_window,
        "_seed_marker": _SEED_MARKER,
    }


def _build_steady_doc(
    *,
    i: int,
    total: int,
    steady_anchor: datetime,
    rng: random.Random,
) -> dict:
    offset_seconds = (total - 1 - i) * _INTERVAL_SECONDS
    recorded_at = steady_anchor - timedelta(seconds=offset_seconds)
    observed_tps = round(rng.uniform(8.0, 25.0), 2)
    p99 = round(rng.uniform(60.0, 110.0), 1)
    p50 = round(rng.uniform(20.0, 40.0), 1)
    p50 = min(p50, p99)
    rule_eval_p99 = round(p99 * 0.7 + rng.uniform(-2.0, 2.0), 1)
    rule_eval_p99 = max(1.0, rule_eval_p99)
    quarantine_per_sec = round(rng.uniform(0.5, 1.5), 2)
    txns_in_window = int(round(observed_tps * _INTERVAL_SECONDS))
    cases_in_window = int(round(quarantine_per_sec * _INTERVAL_SECONDS))

    return {
        "recorded_at": recorded_at,
        "mode": "steady",
        "burst_run_id": None,
        "observed_tps": observed_tps,
        "p50_ms_ingest": p50,
        "p99_ms_ingest": p99,
        "rule_eval_p99_ms": rule_eval_p99,
        "quarantine_per_sec": quarantine_per_sec,
        "txns_in_window": txns_in_window,
        "cases_in_window": cases_in_window,
        "_seed_marker": _SEED_MARKER,
    }


async def main(
    db,
    *,
    samples_burst: int = 60,
    samples_steady: int = 40,
) -> dict:
    """Seed `system_metrics` with one burst run + a steady baseline.

    Returns ``{burst_inserted, steady_inserted, total_inserted, burst_run_id}``.
    """
    coll = db[SYSTEM_METRICS]

    burst_run_id = f"{_SEED_BURST_PREFIX}{date.today().isoformat()}"

    # Idempotency: nuke any prior PR-14 rows. Matches both the
    # day-keyed seed_burst_* run namespace and any steady rows tagged
    # with `_seed_marker = pr14`.
    await coll.delete_many({
        "$or": [
            {"burst_run_id": {"$regex": f"^{_SEED_BURST_PREFIX}"}},
            {"_seed_marker": _SEED_MARKER},
        ]
    })

    rng = random.Random(11)
    anchor = _utcnow()

    # Burst window: spans `samples_burst * 30s` ending at anchor.
    burst_inserted = 0
    for i in range(samples_burst):
        doc = _build_burst_doc(
            i=i,
            total=samples_burst,
            anchor=anchor,
            burst_run_id=burst_run_id,
            rng=rng,
        )
        await coll.insert_one(doc)
        burst_inserted += 1

    # Steady window precedes the burst by at least 1 minute and the
    # full burst duration (so its newest row is < every burst row).
    burst_span = (samples_burst - 1) * _INTERVAL_SECONDS
    steady_anchor = anchor - timedelta(
        seconds=burst_span + _STEADY_GAP_SECONDS,
    )
    steady_inserted = 0
    for i in range(samples_steady):
        doc = _build_steady_doc(
            i=i,
            total=samples_steady,
            steady_anchor=steady_anchor,
            rng=rng,
        )
        await coll.insert_one(doc)
        steady_inserted += 1

    counters = {
        "burst_inserted": burst_inserted,
        "steady_inserted": steady_inserted,
        "total_inserted": burst_inserted + steady_inserted,
        "burst_run_id": burst_run_id,
    }
    logger.info("seed_system_metrics_done", **counters)
    return counters


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed system_metrics fixture rows")
    p.add_argument("--samples-burst", type=int, default=60)
    p.add_argument("--samples-steady", type=int, default=40)
    return p.parse_args()


async def _cli() -> None:
    configure_logging()
    args = _parse_args()
    await connect_mongo()
    try:
        await main(
            get_db(),
            samples_burst=args.samples_burst,
            samples_steady=args.samples_steady,
        )
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_cli())
