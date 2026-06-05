"""On-demand customer metrics refresh service (PR-12).

Thin orchestration layer in front of `MetricsAggregatorService` that
adds:

  - 6h cooldown awareness (mirrors the nightly worker's contract via
    `customer.cross_entity_metrics.last_computed_at`),
  - a `force` bypass for ops/dashboard "recompute now" flows,
  - bounded-concurrency batch refresh,
  - a passthrough wrapper around `get_burst_status` so the route layer
    has a single typed entry point.

The service deliberately does NOT touch worker code or the
aggregator's math — it only decides when to call the aggregator and
how to persist its result on the customer doc, then returns a small
report dict the route layer can serialise straight to JSON.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.errors import CustomerNotFound
from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository
from app.services.metrics_aggregator_service import MetricsAggregatorService

logger = get_logger(__name__)


# Mirror the nightly worker's cooldown — keeps the on-demand and
# scheduled paths from racing each other.
DEFAULT_COOLDOWN_SECONDS: int = 6 * 3600
DEFAULT_MAX_CONCURRENCY: int = 8
BATCH_REFRESH_HARD_CAP: int = 50


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class MetricsRefreshService:
    """Cache-aware orchestration around `MetricsAggregatorService`."""

    def __init__(
        self,
        aggregator: MetricsAggregatorService,
        customer_repo: CustomerRepository,
        *,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._aggregator = aggregator
        self._customers = customer_repo
        self._cooldown_seconds = cooldown_seconds

    # ------------------------------------------------------------------
    # Single-customer refresh
    # ------------------------------------------------------------------

    async def refresh_one(
        self, customer_id: str, *, force: bool = False
    ) -> dict:
        """Refresh `cross_entity_metrics` for one customer.

        Returns:
            {
              customer_id: str,
              computed: bool,                  # True iff aggregator ran
              skipped_reason: str | None,      # "cooldown_active" if skipped
              cross_entity_metrics: dict | None,
            }

        Raises `CustomerNotFound` if the customer doesn't exist —
        the route layer maps that to 404 via `AcmeError` handler.
        """
        customer = await self._customers.get_by_customer_id(customer_id)
        if not customer:
            raise CustomerNotFound(customer_id)

        now = _utcnow()
        if not force and self._is_cooled_down(customer, now=now):
            existing = customer.get("cross_entity_metrics") or None
            logger.info(
                "metrics_refresh_skipped",
                customer_id=customer_id,
                reason="cooldown_active",
            )
            return {
                "customer_id": customer_id,
                "computed": False,
                "skipped_reason": "cooldown_active",
                "cross_entity_metrics": existing,
            }

        metrics = await self._aggregator.compute_for_customer(customer_id)
        if not isinstance(metrics, dict):
            metrics = {}
        metrics.setdefault("last_computed_at", now)

        # Persist the recomputed block back onto the customer doc so
        # the cooldown actually means something next time. We use the
        # repo's collection resolver to dispatch on customer_type, the
        # same idiom the nightly worker uses.
        ctype = customer.get("customer_type")
        coll = self._customers._coll_for(ctype)  # noqa: SLF001
        await coll.update_one(
            {"customer_id": customer_id},
            {"$set": {"cross_entity_metrics": metrics}},
        )
        logger.info(
            "metrics_refresh_computed",
            customer_id=customer_id,
            forced=bool(force),
        )

        return {
            "customer_id": customer_id,
            "computed": True,
            "skipped_reason": None,
            "cross_entity_metrics": metrics,
        }

    # ------------------------------------------------------------------
    # Batch refresh
    # ------------------------------------------------------------------

    async def refresh_many(
        self,
        customer_ids: list[str],
        *,
        force: bool = False,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> dict:
        """Refresh many customers with bounded concurrency.

        Returns:
            {
              requested: int,
              computed: int,
              skipped: int,
              errors: [{customer_id, reason}],
            }

        Per-id failures (including `CustomerNotFound`) are captured in
        the `errors` list so a single bad id doesn't abort the batch.
        """
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _one(cid: str) -> tuple[str, dict | None, str | None]:
            async with sem:
                try:
                    out = await self.refresh_one(cid, force=force)
                    return cid, out, None
                except CustomerNotFound:
                    return cid, None, "not_found"
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "metrics_refresh_batch_error",
                        customer_id=cid,
                        error=str(exc),
                    )
                    return cid, None, str(exc) or exc.__class__.__name__

        results = await asyncio.gather(
            *(_one(cid) for cid in customer_ids), return_exceptions=False
        )

        computed = 0
        skipped = 0
        errors: list[dict] = []
        for cid, out, err in results:
            if err is not None:
                errors.append({"customer_id": cid, "reason": err})
                continue
            if out is None:
                # Defensive — shouldn't happen but keep the shape sane.
                errors.append({"customer_id": cid, "reason": "unknown"})
                continue
            if out["computed"]:
                computed += 1
            else:
                skipped += 1

        return {
            "requested": len(customer_ids),
            "computed": computed,
            "skipped": skipped,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Burst status passthrough
    # ------------------------------------------------------------------

    async def get_burst_status(
        self, *, run_id: str | None = None, limit: int = 60
    ) -> dict:
        """Thin passthrough around the aggregator's burst reader.

        Kept on this service so the route layer has a single
        injection point per concern (refresh + burst). The aggregator
        owns the dual-shape envelope contract.
        """
        return await self._aggregator.get_burst_status(run_id, limit=limit)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_cooled_down(self, customer: dict, *, now: datetime) -> bool:
        metrics = customer.get("cross_entity_metrics") or {}
        last = _coerce_utc(metrics.get("last_computed_at"))
        if not isinstance(last, datetime):
            return False
        return (now - last) < timedelta(seconds=self._cooldown_seconds)


__all__ = [
    "BATCH_REFRESH_HARD_CAP",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_CONCURRENCY",
    "MetricsRefreshService",
]
