"""Platform-health sampler — writes one `system_metrics` doc per minute.

Powers the BurstModeTile (Phase B.3). Reads in-process counters from
`app.core.metrics` and the most-recent `metadata.burst_run_id` seen on
`transactions` to label the sample with the active burst (if any).

Run via:  python -m app.workers.metrics_recorder
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.constants import SYSTEM_METRICS, TRANSACTIONS
from app.core.logging import configure_logging, get_logger
from app.core.metrics import (
    quarantine_window,
    rule_eval_window,
    transactions_window,
)
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetricsRecorder:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._interval = max(5, settings.metrics_recorder_interval_seconds)

    async def run(self) -> None:
        db = get_db()
        coll = db[SYSTEM_METRICS]
        txns = db[TRANSACTIONS]
        logger.info("metrics_recorder_started", interval_seconds=self._interval)
        # Loop on a wakeable sleep so SIGTERM exits promptly.
        while not self._stop.is_set():
            try:
                doc = await self._sample(txns)
                await coll.insert_one(doc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("metrics_recorder_sample_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    async def _sample(self, txns) -> dict:
        now = _utcnow()
        window_start = now - timedelta(seconds=self._interval)

        # Count + scope to the most-recent burst window in this interval.
        recent = await txns.find_one(
            {"metadata.burst_run_id": {"$exists": True},
             "timestamp": {"$gte": window_start.isoformat()}},
            sort=[("timestamp", -1)],
            projection={"_id": 0, "metadata.burst_run_id": 1},
        )
        burst_run_id = (
            (recent or {}).get("metadata", {}).get("burst_run_id")
            if recent else None
        )

        txns_in_window = await txns.count_documents(
            {"timestamp": {"$gte": window_start.isoformat()}}
        )

        observed_tps = transactions_window.rate_per_sec()
        mode = "burst" if burst_run_id else ("steady" if observed_tps > 0 else "idle")

        return {
            "recorded_at": now,
            "mode": mode,
            "burst_run_id": burst_run_id,
            "observed_tps": round(observed_tps, 2),
            "p50_ms_ingest": round(rule_eval_window.percentile_ms(0.50), 1),
            "p99_ms_ingest": round(rule_eval_window.percentile_ms(0.99), 1),
            "quarantine_per_sec": round(quarantine_window.rate_per_sec(), 2),
            "rule_eval_p99_ms": round(rule_eval_window.percentile_ms(0.99), 1),
            "txns_in_window": int(txns_in_window),
            "cases_in_window": int(round(quarantine_window.rate_per_sec() * self._interval)),
        }

    async def shutdown(self) -> None:
        self._stop.set()


async def main() -> None:
    configure_logging()
    await connect_mongo()
    rec = MetricsRecorder()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(rec.shutdown()))
    try:
        await rec.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
