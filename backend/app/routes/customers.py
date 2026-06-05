"""Customer 360 + search endpoints (Pillar 1).

PR-2 wires `customer_index` so the legacy
`/api/customers/{customer_id}` route can answer regardless of whether
the customer lives in `customers_residential` or `customers_commercial`.
The route layer asks the repo, which:
  - Without `STORAGE_SPLIT`: hits the legacy single collection.
  - With `STORAGE_SPLIT`: looks up `customer_index`, then hits the typed
    collection.

Typed convenience routes (`/api/customers/residential/...` and
`/api/customers/commercial/...`) land in PR-12. PR-2 keeps the API
surface stable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import DBDep
from app.repositories.customer_repo import CustomerRepository
from app.repositories.transaction_repo import TransactionRepository
from app.schemas.customer import CustomerRecommendations
from app.services.customer_360_service import Customer360Service
from app.services.customer_refresh_service import CustomerRefreshService
from app.services.customer_service import CustomerService
from app.services.nbo_service import NboService
from app.services.transaction_analytics_service import (
    DAYS_MAX,
    DAYS_MIN,
    TransactionAnalyticsService,
)

router = APIRouter(prefix="/api/customers", tags=["customers"])

# Hard cap on `POST /batch-refresh-360` — protects the API process from
# unbounded fan-out. Surfaced as a module constant so the test layer
# can pin it without re-deriving the magic number.
BATCH_REFRESH_MAX_IDS: int = 50


def _service(db: DBDep) -> CustomerService:
    return CustomerService(CustomerRepository(db))


def _nbo_service(db: DBDep) -> NboService:
    return NboService(CustomerRepository(db))


def _customer_repo(db: DBDep) -> CustomerRepository:
    """Bare `CustomerRepository` for routes that need raw read access
    (e.g. the AutoEmbed semantic search endpoint)."""
    return CustomerRepository(db)


def _profile_service(db: DBDep) -> Customer360Service:
    """PR-9 composition layer for the rich RetailGroup-tier 360 profile.

    Parallel to `_service` — the legacy `/api/customers/{id}` route
    keeps using `CustomerService.get_360` so flips of
    `flags.RICH_CUSTOMER_360` don't disturb existing callers.
    """
    return Customer360Service(CustomerRepository(db))


@router.get("")
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    svc: CustomerService = Depends(_service),
) -> dict:
    items = await svc.page(skip=skip, limit=limit)
    return {"items": items, "skip": skip, "limit": limit}


@router.get("/search")
async def search_customers(
    q: str = Query(..., min_length=1, description="Natural-language description."),
    limit: int = Query(20, ge=1, le=50),
    customer_type: str | None = Query(
        None,
        alias="filter[customer_type]",
        description="Restrict to 'residential' or 'commercial'.",
        pattern="^(residential|commercial)$",
    ),
    tier: str | None = Query(None, alias="filter[tier]"),
    entity: str | None = Query(
        None,
        alias="filter[entity]",
        description="Match against the `entities` array (e.g. acme_paytv).",
    ),
    state: str | None = Query(
        None, alias="filter[state]", description="Service state (e.g. Selangor)."
    ),
    inspect: bool = Query(False, description="Wrap response with `_inspector` envelope."),
    repo: CustomerRepository = Depends(_customer_repo),
) -> dict:
    """AutoEmbed-powered semantic customer search.

    Atlas embeds the query text in-cluster with voyage-4-large (same
    model that indexed `embed_source.text` on each customer) and ranks
    the corpus by cosine similarity. Optional filters push down into
    the `$vectorSearch.filter` clause so Atlas narrows the candidate
    pool before scoring.

    When `inspect=true`, the response is wrapped in
    `{data, _inspector}` so the frontend Inspector panel can render
    the executed `$vectorSearch` pipeline verbatim.
    """
    # Measure end-to-end vector-search latency (Atlas embed + ANN
    # lookup + projection). Surfaced through the inspector envelope so
    # an operator can confirm the index actually responded quickly
    # — previously hard-coded to 0 which masked slow / cold indexes.
    import time as _time
    _t0 = _time.perf_counter()
    hits, pipelines, primary_coll = await repo.vector_search(
        q,
        limit=limit,
        customer_type=customer_type,  # type: ignore[arg-type]
        tier=tier,
        entity=entity,
        state=state,
    )
    _latency_ms = round((_time.perf_counter() - _t0) * 1000.0, 1)

    items: list[dict[str, Any]] = []
    for h in hits:
        addr = h.get("address") or {}
        embed_text = (h.get("embed_source") or {}).get("text")
        summary = embed_text[:240] + "…" if isinstance(embed_text, str) and len(embed_text) > 240 else embed_text
        items.append({
            "customer_id":   h.get("customer_id"),
            "customer_type": h.get("customer_type"),
            "name":          h.get("name"),
            "tier":          h.get("tier"),
            "entities":      h.get("entities") or [],
            "state":         addr.get("state") if isinstance(addr, dict) else None,
            "score":         h.get("score") or 0.0,
            "summary":       summary,
        })

    payload: dict[str, Any] = {"items": items, "query": q}
    if not inspect:
        return payload

    # Inspector envelope — picked up by the frontend `jsonReq` wrapper
    # when the request was sent with `?inspect=true`. We surface the
    # union of pipelines run (one per typed index), but expose only the
    # first one as the canonical `query` so the inspector header reads
    # cleanly; the rest are appended as `additional_pipelines`.
    primary = pipelines[0] if pipelines else None
    inspector_payload: dict[str, Any] = {
        "operation":    "vectorSearch",
        "database":     settings.acme_db,
        "collection":   (primary or {}).get("collection") or primary_coll,
        "query":        (primary or {}).get("pipeline") or [],
        "documents":    items,
        "result_count": len(items),
        "result_bytes": 0,
        "latency_ms":   _latency_ms,
        "index_name":   (primary or {}).get("index_name"),
        "explain": {
            "stage":         "VECTOR_SEARCH",
            "nReturned":     len(items),
            "index_name":    (primary or {}).get("index_name"),
            "model":         "voyage-4-large",
            "similarity":    "cosine",
            "num_candidates": max(limit * 30, 150),
        },
        "agent_trace":  None,
        "hint":         "customers.$vectorSearch",
    }
    if len(pipelines) > 1:
        inspector_payload["additional_pipelines"] = pipelines[1:]
    return {"data": payload, "_inspector": inspector_payload}


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str,
    simulate_crm_lag: bool = Query(False),
    customer_type: str | None = Query(
        None,
        description="Optional explicit dispatch hint: 'residential' or 'commercial'.",
        pattern="^(residential|commercial)$",
    ),
    svc: CustomerService = Depends(_service),
) -> dict:
    return await svc.get_360(
        customer_id,
        simulate_crm_lag=simulate_crm_lag,
        customer_type=customer_type,
    )


@router.get("/{customer_id}/profile")
async def get_customer_profile(
    customer_id: str,
    rich: bool | None = Query(
        None,
        description=(
            "Override the RICH_CUSTOMER_360 feature flag for this request. "
            "When omitted, the flag's current value decides the response shape."
        ),
    ),
    customer_type: str | None = Query(
        None,
        description="Optional explicit dispatch hint: 'residential' or 'commercial'.",
        pattern="^(residential|commercial)$",
    ),
    svc: Customer360Service = Depends(_profile_service),
) -> dict:
    """PR-9 — RetailGroup-tier 360 profile composition.

    Distinct from `GET /api/customers/{customer_id}` (which keeps the
    lean PR-2 envelope). When `rich=true` (or the flag is on) returns
    the rich profile; otherwise returns the lean shape with the same
    contract as the legacy 360.
    """
    return await svc.get_profile(
        customer_id, customer_type=customer_type, rich=rich
    )


@router.get("/{customer_id}/recommendations", response_model=CustomerRecommendations)
async def get_recommendations(
    customer_id: str,
    svc: NboService = Depends(_nbo_service),
) -> CustomerRecommendations:
    """Compute and persist next-best-offer + churn risk projection.

    Read-then-write: the result is `$set` on the customer doc so subsequent
    `/api/customers/{id}` reads include it under `recommendations`.
    """
    return await svc.compute(customer_id)


# ---------------------------------------------------------------------
# PR-12 — On-demand 360 refresh + analytics + embedding status.
#
# All four endpoints are append-only relative to the PR-2/PR-9 surface
# above. They share the same `/api/customers` prefix so the OpenAPI
# grouping stays coherent.
# ---------------------------------------------------------------------


def _refresh_service(db: DBDep) -> CustomerRefreshService:
    """Compose the on-demand 360 refresh service.

    The service is intentionally not stateful — fresh per request is
    fine; the cooldown gate lives on the customer doc.
    """
    repo = CustomerRepository(db)
    return CustomerRefreshService(repo, Customer360Service(repo))


def _analytics_service(db: DBDep) -> TransactionAnalyticsService:
    return TransactionAnalyticsService(
        TransactionRepository(db),
        CustomerRepository(db),
    )


class BatchRefresh360Payload(BaseModel):
    """Body for `POST /api/customers/batch-refresh-360`.

    `customer_ids` is bounded at `BATCH_REFRESH_MAX_IDS` (50) so a
    single call cannot fan out unbounded mongo round-trips. `force`
    bypasses the per-doc cooldown gate.
    """
    customer_ids: list[str] = Field(
        ..., min_length=1, max_length=BATCH_REFRESH_MAX_IDS
    )
    force: bool = False


@router.post("/{customer_id}/refresh-360")
async def refresh_customer_360(
    customer_id: str,
    force: bool = Query(
        False,
        description=(
            "Bypass the 6-hour per-doc cooldown. Useful for analyst-driven"
            " refreshes immediately following a manual data fix."
        ),
    ),
    svc: CustomerRefreshService = Depends(_refresh_service),
) -> dict:
    """Trigger an immediate 360 enrichment pass for one customer.

    Mirrors the per-doc work of the nightly aggregator without
    recomputing embeddings (that's the embedding-refresher worker /
    `/embedding-status` route territory). Returns the canonical
    refresh envelope: `{customer_id, refreshed, skipped_reason,
    computed_at}`. A missing customer surfaces as a 404, not as
    `refreshed=false`, so dashboards can distinguish "couldn't find
    you" from "skipped because cool".
    """
    result = await svc.refresh_one(customer_id, force=force)
    if result.get("skipped_reason") == "not_found":
        raise HTTPException(
            status_code=404,
            detail={"code": "CUSTOMER_NOT_FOUND", "customer_id": customer_id},
        )
    return result


@router.post("/batch-refresh-360")
async def batch_refresh_customer_360(
    payload: BatchRefresh360Payload,
    svc: CustomerRefreshService = Depends(_refresh_service),
) -> dict:
    """Batch variant of `refresh-360`. Cap of 50 ids per call.

    Concurrency is bounded by the service (Semaphore(8)). Per-id
    failures appear in the `errors` list rather than aborting the
    whole batch — analysts can drive a 30-id refresh and discover
    that 2 of them have moved out of the warehouse without losing
    the other 28.
    """
    return await svc.refresh_many(
        payload.customer_ids, force=payload.force
    )


@router.get("/{customer_id}/transaction-pattern")
async def get_transaction_pattern(
    customer_id: str,
    days: int = Query(
        30,
        ge=DAYS_MIN,
        le=DAYS_MAX,
        description="Lookback window in days. Clamped to [1, 365].",
    ),
    svc: TransactionAnalyticsService = Depends(_analytics_service),
) -> dict:
    """Return the customer's recent transaction-pattern summary.

    Wraps `TransactionRepository.customer_pattern`. 404 when the
    customer doesn't exist; the empty-window shape (zero-fill) is
    reserved for "customer exists, no transactions in window".
    """
    pattern = await svc.customer_pattern(customer_id, days=days)
    if pattern is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CUSTOMER_NOT_FOUND", "customer_id": customer_id},
        )
    return pattern


@router.get("/{customer_id}/embedding-status")
async def get_embedding_status(
    customer_id: str,
    svc: CustomerRefreshService = Depends(_refresh_service),
) -> dict:
    """Read-only snapshot of the customer's embedding freshness.

    Surfaces dim, generated_at, age_seconds, and the staleness verdict
    (`is_stale`) using the same threshold the embedding-refresher
    worker uses. The dashboard uses this to decide whether to nudge
    the worker; embeddings themselves are NEVER returned over HTTP.
    """
    status = await svc.embedding_status(customer_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CUSTOMER_NOT_FOUND", "customer_id": customer_id},
        )
    return status
