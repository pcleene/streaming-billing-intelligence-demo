"""Metrics endpoints (PR-12).

Exposes `MetricsAggregatorService` capabilities + burst status via HTTP:

  - POST /api/customers/{customer_id}/metrics/refresh
        Recompute `cross_entity_metrics` for one customer with a 6h
        cooldown (force=true bypasses).
  - POST /api/customers/metrics/batch-refresh
        Recompute up to 50 customers with bounded concurrency.
  - GET  /api/metrics/burst
        Read the most-recent (or run-scoped) burst window envelope.

All three routes share the same `MetricsRefreshService` dependency so
the cooldown/cache contract stays consistent across paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.deps import DBDep
from app.repositories.customer_repo import CustomerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.services.metrics_aggregator_service import MetricsAggregatorService
from app.services.metrics_refresh_service import (
    BATCH_REFRESH_HARD_CAP,
    MetricsRefreshService,
)

logger = get_logger(__name__)


# Two routers — one for the customer-scoped refresh paths (kept under
# /api/customers/* so their URL space matches the existing customer
# routes), and one for the global /api/metrics/* burst reader. Both
# are wired into the public `router` at the bottom of this module so
# the route decorators below have a chance to register first.
customer_router = APIRouter(prefix="/api/customers", tags=["metrics"])
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# ---------------------------------------------------------------------
# Dependency wiring
# ---------------------------------------------------------------------


def _refresh_service(db: DBDep) -> MetricsRefreshService:
    """Compose the service graph from a single DB handle.

    Mirrors the wiring style used in `app/routes/customers.py` so the
    dependency override surface is uniform across route modules.
    """
    txn_repo = TransactionRepository(db)
    aggregator = MetricsAggregatorService(txn_repo, db=db)
    customer_repo = CustomerRepository(db)
    return MetricsRefreshService(aggregator, customer_repo)


# ---------------------------------------------------------------------
# Request / response schemas (Pydantic v2)
# ---------------------------------------------------------------------


class RefreshOneResponse(BaseModel):
    customer_id: str
    computed: bool
    skipped_reason: str | None = None
    cross_entity_metrics: dict[str, Any] | None = None


class BatchRefreshRequest(BaseModel):
    customer_ids: list[str] = Field(
        ..., min_length=1, max_length=BATCH_REFRESH_HARD_CAP
    )
    force: bool = False


class BatchRefreshError(BaseModel):
    customer_id: str
    reason: str


class BatchRefreshResponse(BaseModel):
    requested: int
    computed: int
    skipped: int
    errors: list[BatchRefreshError]


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------


@customer_router.post(
    "/{customer_id}/metrics/refresh",
    response_model=RefreshOneResponse,
)
async def refresh_customer_metrics(
    customer_id: str,
    force: bool = Query(
        False,
        description="Bypass the 6h cooldown and recompute unconditionally.",
    ),
    svc: MetricsRefreshService = Depends(_refresh_service),
) -> RefreshOneResponse:
    """Recompute one customer's `cross_entity_metrics`.

    Returns the freshly-computed block — or, when the cooldown is
    active and `force=false`, the existing block plus
    `skipped_reason="cooldown_active"`.
    """
    out = await svc.refresh_one(customer_id, force=force)
    return RefreshOneResponse(**out)


@customer_router.post(
    "/metrics/batch-refresh",
    response_model=BatchRefreshResponse,
)
async def batch_refresh_customer_metrics(
    body: BatchRefreshRequest,
    svc: MetricsRefreshService = Depends(_refresh_service),
) -> BatchRefreshResponse:
    """Recompute many customers' metrics with bounded concurrency.

    Per-customer errors don't abort the batch; they surface in the
    `errors` list with a short reason so the dashboard can render
    a partial-success report.
    """
    out = await svc.refresh_many(body.customer_ids, force=body.force)
    return BatchRefreshResponse(
        requested=out["requested"],
        computed=out["computed"],
        skipped=out["skipped"],
        errors=[BatchRefreshError(**e) for e in out["errors"]],
    )


@metrics_router.get("/burst")
async def get_burst(
    run_id: str | None = Query(
        None,
        description="Restrict to a specific burst run; default = latest.",
    ),
    limit: int = Query(
        60, ge=1, le=720,
        description="Max samples to return (default 60, max 720).",
    ),
    svc: MetricsRefreshService = Depends(_refresh_service),
) -> dict:
    """Burst-window envelope passthrough.

    Returns the dual-shape envelope produced by
    `MetricsAggregatorService.get_burst_status` as-is — no rewrapping,
    so dashboards on either side of the PR-11 sister contract keep
    working.
    """
    return await svc.get_burst_status(run_id=run_id, limit=limit)


# Public router — include the sub-routers AFTER the decorators above
# have registered their routes. `app/main.py` does
# `app.include_router(metrics.router)` against this single handle.
router = APIRouter()
router.include_router(customer_router)
router.include_router(metrics_router)


__all__ = [
    "router",
    "customer_router",
    "metrics_router",
    "RefreshOneResponse",
    "BatchRefreshRequest",
    "BatchRefreshResponse",
    "BatchRefreshError",
]
