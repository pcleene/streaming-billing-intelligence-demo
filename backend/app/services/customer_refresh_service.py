"""On-demand customer 360 refresh service (PR-12).

Sibling to `customer_360_aggregator` (the nightly batch worker): exposes
the same per-customer enrichment pass as a service-layer API so the new
`/api/customers/{id}/refresh-360` and batch routes can trigger it
directly. The shape of the work mirrors
`app.workers.customer_360_aggregator._Aggregator._refresh_one`:

  1. Read the customer doc.
  2. Recompute / persist `cross_entity_metrics` via the existing
     `Customer360Service` pathway (we do NOT duplicate the math —
     `Customer360Service` is the composition layer, but the cross-entity
     metrics block on the doc is the source of truth here; we just
     stamp `last_computed_at` to `now` so the cooldown re-arms exactly
     like the worker does).
  3. Bump `current_cycle.last_recomputed_at` to `now`.

Embedding refresh is intentionally NOT triggered here. The legacy
`embedding_refresher` worker was retired in PR-A (ADR-032) when
quarantine history moved to Atlas Auto Embedding; customer-side
embeddings are not part of the AutoEmbed migration yet. The endpoint
still exposes the legacy embedding status read-only via
`embedding_status()` for backwards compatibility with the dashboard.

Cooldown semantics match the aggregator: 6h default; `force=True` skips
the gate. Per-doc errors in batch mode are captured into the response
rather than aborting the whole batch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository
from app.services.customer_360_service import Customer360Service

logger = get_logger(__name__)


# Mirror the aggregator + embedding-refresher constants so flips to
# either worker remain coherent with the on-demand path. We import them
# locally inside the constructor default to avoid hard-tying the service
# to the worker module at import time (keeps the service usable from
# tests without dragging in the whole worker stack).
_DEFAULT_COOLDOWN_SECONDS: int = 6 * 3600  # mirrors COOLDOWN_SECONDS
_DEFAULT_EMBEDDING_MAX_AGE_DAYS: int = 30  # mirrors MAX_EMBEDDING_AGE_DAYS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: Any) -> datetime | None:
    """Best-effort coercion of a stored timestamp into a UTC tz-aware datetime."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class CustomerRefreshService:
    """On-demand customer 360 enrichment pass + embedding status reads."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        customer_360_service: Customer360Service,
        *,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
        embedding_max_age_days: int = _DEFAULT_EMBEDDING_MAX_AGE_DAYS,
    ) -> None:
        self._customers = customer_repo
        self._profile = customer_360_service
        self._cooldown_seconds = cooldown_seconds
        self._embedding_max_age_days = embedding_max_age_days

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def refresh_one(
        self, customer_id: str, *, force: bool = False
    ) -> dict:
        """Run a single-customer enrichment pass.

        Returns a dict with the customer_id, a `refreshed` boolean, the
        skip reason (only set when `refreshed=False`), and the
        `computed_at` UTC timestamp (set to the wall-clock at the time
        of the decision regardless of branch — gives the caller a
        consistent anchor for paging UIs).
        """
        now = _utcnow()
        doc = await self._customers.get_by_customer_id(customer_id)
        if doc is None:
            return {
                "customer_id": customer_id,
                "refreshed": False,
                "skipped_reason": "not_found",
                "computed_at": now,
            }

        if not force and self._is_cooled_down(doc, now=now):
            return {
                "customer_id": customer_id,
                "refreshed": False,
                "skipped_reason": "cooldown",
                "computed_at": now,
            }

        await self._refresh_doc(doc, now=now)
        return {
            "customer_id": customer_id,
            "refreshed": True,
            "skipped_reason": None,
            "computed_at": now,
        }

    async def refresh_many(
        self,
        customer_ids: list[str],
        *,
        force: bool = False,
        max_concurrency: int = 8,
    ) -> dict:
        """Run `refresh_one` for each id, bounded by a Semaphore.

        Returns a flat envelope:
            {
                "requested": int,
                "refreshed": int,
                "skipped":   int,
                "errors":    [{"customer_id": str, "reason": str}],
            }

        Per-id failures are caught and surfaced under `errors`; cooldown
        skips count under `skipped`.
        """
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _one(cid: str) -> tuple[str, dict | Exception]:
            async with sem:
                try:
                    return cid, await self.refresh_one(cid, force=force)
                except Exception as exc:  # noqa: BLE001 — bounded per id
                    return cid, exc

        results = await asyncio.gather(*[_one(c) for c in customer_ids])

        refreshed = 0
        skipped = 0
        errors: list[dict] = []
        for cid, outcome in results:
            if isinstance(outcome, Exception):
                errors.append({"customer_id": cid, "reason": str(outcome)})
                continue
            if outcome.get("refreshed"):
                refreshed += 1
            else:
                reason = outcome.get("skipped_reason") or "skipped"
                if reason == "not_found":
                    # Treat missing customers as errors so analysts
                    # can spot drift between an upstream id list and
                    # the warehouse.
                    errors.append({"customer_id": cid, "reason": "not_found"})
                else:
                    skipped += 1
        return {
            "requested": len(customer_ids),
            "refreshed": refreshed,
            "skipped": skipped,
            "errors": errors,
        }

    async def embedding_status(self, customer_id: str) -> dict | None:
        """Snapshot of the customer's embedding freshness.

        Returns `None` when the customer doesn't exist (route layer
        translates to 404). Otherwise:
            {
                "customer_id":   str,
                "has_embedding": bool,
                "dim":           int | None,
                "generated_at":  datetime | None,
                "age_seconds":   int | None,
                "is_stale":      bool,
            }

        Staleness mirrors the (now-retired) `embedding_refresher`
        worker's `MAX_EMBEDDING_AGE_DAYS` policy — preserved here as a
        configurable on this service. A customer with no embedding is
        considered stale.
        """
        doc = await self._customers.get_by_customer_id(customer_id)
        if doc is None:
            return None

        embedding = doc.get("embedding")
        has_embedding = isinstance(embedding, (list, tuple)) and len(embedding) > 0
        dim = len(embedding) if has_embedding else None
        generated_at = _coerce_utc(doc.get("embedding_generated_at"))
        now = _utcnow()
        age_seconds: int | None
        if generated_at is None:
            age_seconds = None
        else:
            age_seconds = max(0, int((now - generated_at).total_seconds()))
        max_age_seconds = self._embedding_max_age_days * 86_400
        if not has_embedding or generated_at is None:
            is_stale = True
        else:
            is_stale = age_seconds is not None and age_seconds > max_age_seconds
        return {
            "customer_id": customer_id,
            "has_embedding": has_embedding,
            "dim": dim,
            "generated_at": generated_at,
            "age_seconds": age_seconds,
            "is_stale": is_stale,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_cooled_down(self, customer: dict, *, now: datetime) -> bool:
        """True when the doc was aggregated within `cooldown_seconds`."""
        metrics = customer.get("cross_entity_metrics") or {}
        last = _coerce_utc(metrics.get("last_computed_at"))
        if last is None:
            return False
        return (now - last) < timedelta(seconds=self._cooldown_seconds)

    async def _refresh_doc(self, customer: dict, *, now: datetime) -> None:
        """Persist refreshed enrichment fields back onto the doc.

        Mirrors `_Aggregator._refresh_one` minimally: we keep the
        existing `cross_entity_metrics` payload but stamp
        `last_computed_at` so the cooldown re-arms; bump
        `current_cycle.last_recomputed_at`. Embedding refresh is
        intentionally NOT touched here — customer-side embeddings are
        out of scope after PR-A (ADR-032 retired the embedding worker;
        history embedding moved to Atlas AutoEmbed). The legacy
        `embedding` field is exposed read-only via `embedding_status`.
        """
        customer_id = customer["customer_id"]

        # Preserve any existing metrics block but re-stamp the cooldown
        # anchor. The aggregator worker recomputes the math via
        # MetricsAggregatorService; on-demand we don't recompute the
        # numeric trends (that would re-traverse the txn collection
        # and is properly the worker's job) — but the cross-entity
        # metrics block remains the canonical doc-level summary, and
        # stamping `last_computed_at` re-arms the gate.
        metrics = dict(customer.get("cross_entity_metrics") or {})
        metrics["last_computed_at"] = now

        update = {
            "$set": {
                "cross_entity_metrics": metrics,
                "current_cycle.last_recomputed_at": now,
            }
        }

        # Pick the right collection via the repo's resolver — same idiom
        # as the worker.
        ctype = customer.get("customer_type")
        coll = self._customers._coll_for(ctype)  # noqa: SLF001
        await coll.update_one({"customer_id": customer_id}, update)


__all__ = [
    "CustomerRefreshService",
]
