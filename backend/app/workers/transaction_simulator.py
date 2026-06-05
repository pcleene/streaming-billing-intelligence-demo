"""MSK transaction simulator — separate process entry point.

Generates Acme-Malaysia-shaped billing events at a configurable TPS,
sprinkled with deliberate anomalies (geographic, velocity, duplicates,
discount-without-promo, PPV-without-entitlement) so the rule engine has
something to fire on. Publishes to MSK; ASP picks them up.

Run via:  python -m app.workers.transaction_simulator
Burst:    python -m app.workers.transaction_simulator --burst \\
              --burst-target-tps 200 --burst-duration-seconds 300

Burst mode (Phase B.3) ramps from `simulator_tps` up to
`--burst-target-tps` over `--burst-ramp-seconds`, holds for
`--burst-duration-seconds`, then ramps back down. Every emitted event
during the burst window carries `metadata.burst_run_id` so downstream
analytics can scope queries to the run.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import transactions_window
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.streaming.msk_client import MSKProducer
from app.streaming.topic_admin import ensure_topic
from app.workers.transaction_event_factory import build_v3_event_payload

logger = get_logger(__name__)



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BurstPlan:
    """Linear ramp-up → hold → ramp-down TPS schedule.

    Plan is expressed in monotonic seconds since `start_at`. Outside the
    plan window the simulator falls back to the steady-state TPS.
    """

    def __init__(
        self,
        *,
        baseline_tps: int,
        target_tps: int,
        duration_seconds: int,
        ramp_seconds: int,
        start_at: float,
    ) -> None:
        self.baseline_tps = max(1, baseline_tps)
        self.target_tps = max(self.baseline_tps, target_tps)
        self.duration_seconds = max(1, duration_seconds)
        self.ramp_seconds = max(0, ramp_seconds)
        self.start_at = start_at
        self.run_id = f"burst_{uuid.uuid4().hex[:12]}"

    @property
    def end_at(self) -> float:
        return self.start_at + self.duration_seconds + 2 * self.ramp_seconds

    def phase(self, now: float) -> str:
        if now < self.start_at:
            return "pre"
        if now < self.start_at + self.ramp_seconds:
            return "ramp_up"
        if now < self.start_at + self.ramp_seconds + self.duration_seconds:
            return "hold"
        if now < self.end_at:
            return "ramp_down"
        return "post"

    def tps_at(self, now: float) -> int:
        phase = self.phase(now)
        if phase in ("pre", "post"):
            return self.baseline_tps
        if phase == "hold":
            return self.target_tps
        if phase == "ramp_up":
            elapsed = now - self.start_at
            frac = elapsed / self.ramp_seconds if self.ramp_seconds else 1.0
            return int(self.baseline_tps + frac * (self.target_tps - self.baseline_tps))
        # ramp_down
        elapsed = now - (self.start_at + self.ramp_seconds + self.duration_seconds)
        frac = elapsed / self.ramp_seconds if self.ramp_seconds else 1.0
        return int(self.target_tps - frac * (self.target_tps - self.baseline_tps))

    def is_active(self, now: float) -> bool:
        return self.start_at <= now < self.end_at


class Simulator:
    def __init__(self, burst_plan: BurstPlan | None = None) -> None:
        self._producer = MSKProducer()
        self._stop = asyncio.Event()
        self._customers: list[dict] = []
        self._burst = burst_plan

    async def start(self) -> None:
        await connect_mongo()
        await ensure_topic(
            settings.kafka_topic,
            partitions=settings.kafka_topic_partitions,
            replication_factor=settings.kafka_topic_replication,
        )
        await self._producer.start()
        await self._load_customers()
        logger.info(
            "simulator_ready",
            customers=len(self._customers),
            tps=settings.simulator_tps,
            burst_run_id=(self._burst.run_id if self._burst else None),
        )

    async def _load_customers(self) -> None:
        db = get_db()
        # V3 factory needs `customer_type`, `subscriptions`, and
        # `early_termination_fee_myr` in addition to the legacy fields.
        # Tolerate both the legacy single-collection layout and the PR-2
        # split (residential + commercial) by reading `customer_index`
        # via the legacy alias `customers` view if the deployment still
        # has it; otherwise fall back to a union of the typed colls.
        projection = {
            "_id": 0,
            "customer_id": 1,
            "customer_type": 1,
            "address": 1,
            "active_promotions": 1,
            "subscriptions": 1,
            "early_termination_fee_myr": 1,
            "business_profile": 1,
            "entitlements": 1,
        }
        cursor = db["customers"].find({}, projection).limit(2000)
        self._customers = [c async for c in cursor]
        if not self._customers:
            logger.warning("no_customers_in_db_simulator_will_skip")

    async def run(self) -> None:
        if not self._customers:
            return
        last_phase = None
        while not self._stop.is_set():
            now = time.time()
            tps = self._current_tps(now)
            phase = self._current_phase(now)
            if phase != last_phase:
                logger.info(
                    "simulator_phase_change",
                    phase=phase,
                    tps=tps,
                    burst_run_id=(self._burst.run_id if self._burst else None),
                )
                last_phase = phase
            await self._emit_one(now)
            transactions_window.record()
            period = 1.0 / max(1, tps)
            await asyncio.sleep(period)
            if self._burst and phase == "post":
                # Burst plan is one-shot — exit cleanly when it finishes.
                logger.info("burst_complete", run_id=self._burst.run_id)
                self._stop.set()

    def _current_tps(self, now: float) -> int:
        if self._burst and self._burst.is_active(now):
            return self._burst.tps_at(now)
        return settings.simulator_tps

    def _current_phase(self, now: float) -> str:
        if not self._burst:
            return "steady"
        return self._burst.phase(now)

    async def _emit_one(self, now: float) -> None:
        customer = random.choice(self._customers)
        anomaly = random.random() < settings.simulator_anomaly_rate
        event = self._build_event(customer, anomaly=anomaly, now=now)
        try:
            await self._producer.send(settings.kafka_topic, event, key=event["customer_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("simulator_send_failed", error=str(exc))

    def _build_event(self, customer: dict, *, anomaly: bool, now: float) -> dict[str, Any]:
        ts = _utcnow()
        event = build_v3_event_payload(customer, ts=ts, anomaly=anomaly)
        # Wire-friendly: Kafka payload JSON-serialises strings, not datetimes.
        event["timestamp"] = ts.isoformat()
        event["source"] = "simulator"
        metadata = event.setdefault("metadata", {})
        if self._burst and self._burst.is_active(now):
            metadata["burst_run_id"] = self._burst.run_id
            metadata["burst_phase"] = self._burst.phase(now)
        return event

    async def shutdown(self) -> None:
        self._stop.set()
        await self._producer.stop()
        await disconnect_mongo()
        logger.info("simulator_stopped")


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the simulator CLI.

    Extracted from `_parse_args` so tests can introspect the flag
    surface without invoking `sys.argv`.
    """
    parser = argparse.ArgumentParser(prog="transaction_simulator")
    parser.add_argument("--burst", action="store_true",
                        help="Run an end-of-month burst then exit.")
    parser.add_argument("--burst-target-tps", type=int,
                        default=settings.burst_target_tps,
                        help="Peak TPS during the hold phase.")
    parser.add_argument("--burst-duration-seconds", type=int,
                        default=settings.burst_duration_seconds,
                        help="How long to hold at target TPS.")
    parser.add_argument("--burst-ramp-seconds", type=int,
                        default=settings.burst_ramp_seconds,
                        help="Ramp-up + ramp-down length in seconds.")
    return parser


def _parse_args() -> argparse.Namespace:
    return _build_arg_parser().parse_args()


async def main() -> None:
    configure_logging()
    args = _parse_args()
    plan: BurstPlan | None = None
    if args.burst:
        plan = BurstPlan(
            baseline_tps=settings.simulator_tps,
            target_tps=args.burst_target_tps,
            duration_seconds=args.burst_duration_seconds,
            ramp_seconds=args.burst_ramp_seconds,
            start_at=time.time(),
        )
        logger.info(
            "burst_plan",
            run_id=plan.run_id,
            baseline_tps=plan.baseline_tps,
            target_tps=plan.target_tps,
            duration_seconds=plan.duration_seconds,
            ramp_seconds=plan.ramp_seconds,
        )
    sim = Simulator(burst_plan=plan)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(sim.shutdown()))
    await sim.start()
    try:
        await sim.run()
    finally:
        await sim.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
