"""Nightly customer 360 aggregator.

Walks every customer document (across `customers_residential` AND
`customers_commercial` when `STORAGE_SPLIT` is on; legacy `customers`
when off) and refreshes three derived fields on each:

1. `cross_entity_metrics` — recomputed via
   `MetricsAggregatorService.compute_for_customer(customer_id)` (PR-9
   sibling deliverable).
2. `interaction_history.channel_engagement_rates` — derived purely from
   `interaction_history.support_tickets` and
   `interaction_history.marketing_events` arrays already on the
   document. Three rates: `support_response_rate`, `marketing_open_rate`,
   `marketing_click_rate`.
3. `current_cycle.last_recomputed_at` — wall-clock stamp for downstream
   freshness checks (and a hook for future incremental cycle math).

A 6h cooldown — derived from `cross_entity_metrics.last_computed_at` —
prevents the worker from thrashing customers that were aggregated by
another agent (e.g. the `/api/customers/{id}/360` route on a cache miss)
in the recent past.

Mirrors the loop / signal-handling skeleton of
`app.workers.case_lifecycle_worker`. Runs by default once per day; the
interval is constructor-injectable so unit tests can drive a fast loop.

Run via:  python -m app.workers.customer_360_aggregator
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta, timezone

from app.core.constants import CUSTOMERS, CUSTOMERS_COMMERCIAL, CUSTOMERS_RESIDENTIAL
from app.core.feature_flags import flags
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.customer_repo import CustomerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.services.metrics_aggregator_service import MetricsAggregatorService

logger = get_logger(__name__)

AGGREGATOR_INTERVAL_SECONDS: int = 86_400  # 1 day
COOLDOWN_SECONDS: int = 6 * 3600  # 6 hours


def _compute_engagement_rates(interaction_history: dict | None) -> dict:
    """Derive channel-engagement rates from raw interaction arrays.

    All three rates default to 0.0 when the corresponding array is
    empty / missing — that's the contract the API layer expects so it
    never has to guard against `None` ratios in dashboards.
    """
    interaction_history = interaction_history or {}
    tickets = interaction_history.get("support_tickets") or []
    events = interaction_history.get("marketing_events") or []

    total_tickets = len(tickets)
    if total_tickets:
        resolved = sum(
            1 for t in tickets
            if isinstance(t, dict) and t.get("status") == "resolved"
        )
        support_response_rate = resolved / total_tickets
    else:
        support_response_rate = 0.0

    total_events = len(events)
    if total_events:
        opens = sum(
            1 for e in events
            if isinstance(e, dict) and e.get("event_type") == "opened"
        )
        clicks = sum(
            1 for e in events
            if isinstance(e, dict) and e.get("event_type") == "clicked"
        )
        marketing_open_rate = opens / total_events
        marketing_click_rate = clicks / total_events
    else:
        marketing_open_rate = 0.0
        marketing_click_rate = 0.0

    return {
        "support_response_rate": support_response_rate,
        "marketing_open_rate": marketing_open_rate,
        "marketing_click_rate": marketing_click_rate,
    }


def _is_cooled_down(
    customer: dict, *, now: datetime, cooldown_seconds: int
) -> bool:
    """Return True when the customer was aggregated within `cooldown_seconds`."""
    metrics = customer.get("cross_entity_metrics") or {}
    last = metrics.get("last_computed_at")
    if not isinstance(last, datetime):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last) < timedelta(seconds=cooldown_seconds)


class _Aggregator:
    def __init__(
        self,
        *,
        customer_repo: CustomerRepository,
        metrics_aggregator: MetricsAggregatorService,
        interval_seconds: int = AGGREGATOR_INTERVAL_SECONDS,
        cooldown_seconds: int = COOLDOWN_SECONDS,
    ) -> None:
        self._repo = customer_repo
        self._metrics = metrics_aggregator
        self._interval = interval_seconds
        self._cooldown_seconds = cooldown_seconds
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Public loop API
    # ------------------------------------------------------------------

    async def run_once(self) -> dict:
        counters = {"scanned": 0, "updated": 0, "skipped": 0, "errors": 0}
        now = datetime.now(timezone.utc)
        async for customer in self._iter_all_customers():
            counters["scanned"] += 1
            customer_id = customer.get("customer_id")
            if not customer_id:
                counters["skipped"] += 1
                continue
            if _is_cooled_down(
                customer, now=now, cooldown_seconds=self._cooldown_seconds
            ):
                counters["skipped"] += 1
                continue
            try:
                await self._refresh_one(customer, now=now)
                counters["updated"] += 1
            except Exception as exc:  # noqa: BLE001 — bounded by per-doc scope
                counters["errors"] += 1
                logger.warning(
                    "customer_360_aggregator_failed",
                    customer_id=customer_id,
                    error=str(exc),
                )
        logger.info(
            "customer_360_aggregator_tick",
            scanned=counters["scanned"],
            updated=counters["updated"],
            skipped=counters["skipped"],
            errors=counters["errors"],
        )
        return counters

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
            except asyncio.TimeoutError:
                continue

    async def shutdown(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _iter_all_customers(self):
        """Yield every customer doc from the active customer collection(s).

        Dispatches on `STORAGE_SPLIT` exactly like `CustomerRepository`:
        when split is on, scan both typed collections; when off, scan
        the legacy alias.
        """
        if flags.STORAGE_SPLIT:
            for coll in (
                self._repo._coll_residential,  # noqa: SLF001 — repo-internal handles
                self._repo._coll_commercial,   # noqa: SLF001
            ):
                cursor = coll.find({})
                async for doc in cursor:
                    yield doc
        else:
            cursor = self._repo._coll.find({})  # noqa: SLF001
            async for doc in cursor:
                yield doc

    async def _refresh_one(self, customer: dict, *, now: datetime) -> None:
        customer_id = customer["customer_id"]

        # 1. cross_entity_metrics — delegated to the service. The service
        # is responsible for producing the full sub-doc (12-month LTV
        # trend, top entity, etc.); we just stamp last_computed_at so
        # the cooldown works on subsequent ticks.
        metrics = await self._metrics.compute_for_customer(customer_id)
        if not isinstance(metrics, dict):
            metrics = {}
        metrics.setdefault("last_computed_at", now)

        # 2. channel_engagement_rates — derived locally from arrays
        # already on the document.
        rates = _compute_engagement_rates(customer.get("interaction_history"))

        # 3. current_cycle freshness stamp — try a repo helper, fall
        # back to a $set on the timestamp.
        cycle_set: dict = {"current_cycle.last_recomputed_at": now}
        refresh_helper = getattr(self._repo, "refresh_current_cycle", None)
        if callable(refresh_helper):
            try:
                await refresh_helper(customer_id)
                # Helper owns the cycle write; we still stamp the
                # timestamp so all three field-sets are merged in one
                # update_one (predictable test assertions).
            except Exception:  # noqa: BLE001 — helper may be best-effort
                logger.debug(
                    "customer_360_refresh_cycle_helper_failed",
                    customer_id=customer_id,
                )

        update = {
            "$set": {
                "cross_entity_metrics": metrics,
                "interaction_history.channel_engagement_rates": rates,
                **cycle_set,
            }
        }

        # Pick the right collection: the repo's resolver already does
        # exactly the dispatch we need.
        ctype = customer.get("customer_type")
        coll = self._repo._coll_for(ctype)  # noqa: SLF001
        await coll.update_one({"customer_id": customer_id}, update)


async def main() -> None:
    configure_logging()
    await connect_mongo()
    db = get_db()
    repo = CustomerRepository(db)
    txn_repo = TransactionRepository(db)
    metrics = MetricsAggregatorService(txn_repo)
    aggregator = _Aggregator(customer_repo=repo, metrics_aggregator=metrics)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(aggregator.shutdown())
        )
    try:
        await aggregator.run()
    finally:
        await disconnect_mongo()


# Re-exports for the test layer / external diagnostics.
__all__ = [
    "AGGREGATOR_INTERVAL_SECONDS",
    "COOLDOWN_SECONDS",
    "CUSTOMERS",
    "CUSTOMERS_RESIDENTIAL",
    "CUSTOMERS_COMMERCIAL",
    "_Aggregator",
    "_compute_engagement_rates",
    "_is_cooled_down",
    "main",
]


if __name__ == "__main__":
    asyncio.run(main())
