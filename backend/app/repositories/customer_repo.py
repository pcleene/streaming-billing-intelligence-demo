"""Customer repository — type-aware (PR-2) + Atlas Search.

Behaviour is controlled by `FeatureFlags.STORAGE_SPLIT`:

- `STORAGE_SPLIT = False` (default until PR-2 migration completes):
  legacy single-collection behaviour. All reads/writes target
  `CUSTOMERS` (which is an alias for `CUSTOMERS_RESIDENTIAL`).
- `STORAGE_SPLIT = True`: reads/writes dispatch via `customer_index`.
  `customer_index` is a thin routing collection mapping `customer_id`
  to its `(customer_type, account_id, parent_account_id)` so the API
  layer can answer `/api/customers/{customer_id}` without knowing the
  type up front.

The public API is preserved — existing callers (`CustomerService`,
`NboService`) keep working without code changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pymongo.asynchronous.database import AsyncDatabase

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    EMBED_SOURCE_PATH,
    IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
    IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
    IDX_CUSTOMERS_SEARCH,
)
from app.core.feature_flags import flags
from app.core.logging import get_logger
from app.repositories.base import BaseRepository

logger = get_logger(__name__)

CustomerType = Literal["residential", "commercial"]


class CustomerRepository(BaseRepository):
    """Type-aware customer repo.

    Default `COLLECTION_NAME` is `CUSTOMERS` (the residential alias) so
    legacy callers that don't yet know about the split keep working. New
    code paths that know the type pass `customer_type=` to dispatch
    directly.
    """

    COLLECTION_NAME = CUSTOMERS

    def __init__(self, db: AsyncDatabase) -> None:
        super().__init__(db)
        # Hold per-type collection handles so methods can dispatch
        # without re-resolving every call.
        self._coll_residential = db[CUSTOMERS_RESIDENTIAL]
        self._coll_commercial = db[CUSTOMERS_COMMERCIAL]
        self._coll_index = db[CUSTOMER_INDEX]

    # --- Type dispatch ------------------------------------------------

    def _coll_for(self, customer_type: CustomerType | None):
        if customer_type == "commercial":
            return self._coll_commercial
        if customer_type == "residential":
            return self._coll_residential
        # Type-unknown — legacy behaviour.
        return self._coll

    async def _resolve_type(self, customer_id: str) -> CustomerType | None:
        """Look up `customer_type` via `customer_index`.

        Returns `None` if the customer isn't indexed yet (which happens
        before the PR-2 migration completes). The caller falls back to
        the legacy collection in that case.
        """
        if not flags.STORAGE_SPLIT:
            return None
        doc = await self._coll_index.find_one(
            {"customer_id": customer_id},
            {"_id": 0, "customer_type": 1},
        )
        if doc and doc.get("customer_type") in ("residential", "commercial"):
            return doc["customer_type"]  # type: ignore[return-value]
        return None

    # --- Reads --------------------------------------------------------

    async def get_by_customer_id(
        self, customer_id: str, customer_type: CustomerType | None = None
    ) -> dict | None:
        """Single-document fetch.

        If `customer_type` is provided, hits the typed collection
        directly. Otherwise, when `STORAGE_SPLIT` is on, dispatches via
        `customer_index`. When the flag is off, hits the legacy alias.
        """
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        return await coll.find_one({"customer_id": customer_id})

    async def get_by_account_id(
        self, account_id: str, customer_type: CustomerType | None = None
    ) -> dict | None:
        if customer_type is not None:
            return await self._coll_for(customer_type).find_one({"account_id": account_id})
        if flags.STORAGE_SPLIT:
            # Try residential first, then commercial.
            doc = await self._coll_residential.find_one({"account_id": account_id})
            if doc:
                return doc
            return await self._coll_commercial.find_one({"account_id": account_id})
        return await self._coll.find_one({"account_id": account_id})

    async def search(self, query: str, *, limit: int = 20) -> list[dict]:
        """Atlas Search autocomplete + fuzzy. NO regex (ADR-009).

        The search index is attached to the residential collection (which
        is the alias target of the legacy `customers` index name), so the
        collection used here is the residential one regardless of flag.
        Commercial-customer search is out of scope for this route in PR-2;
        commercial is queried via `/api/customers/commercial/...` once
        PR-12 lands.
        """
        if not query.strip():
            return []

        pipeline: list[dict] = [
            {
                "$search": {
                    "index": IDX_CUSTOMERS_SEARCH,
                    "compound": {
                        "should": [
                            {"autocomplete": {"query": query, "path": "name", "fuzzy": {"maxEdits": 1}}},
                            {"text": {"query": query, "path": "ic_number"}},
                            {"text": {"query": query, "path": "account_id"}},
                            {"text": {"query": query, "path": "email"}},
                        ],
                        "minimumShouldMatch": 1,
                    },
                }
            },
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "customer_id": 1,
                    "name": 1,
                    "ic_number": 1,
                    "account_id": 1,
                    "segment": 1,
                    "address.state": 1,
                    "total_monthly_value_myr": 1,
                    "score": {"$meta": "searchScore"},
                }
            },
        ]
        try:
            cursor = await self._coll_residential.aggregate(pipeline)
            return [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "customer_search_fallback",
                index=IDX_CUSTOMERS_SEARCH,
                error=str(exc),
            )
            doc = await self._coll_residential.find_one(
                {
                    "$or": [
                        {"customer_id": query},
                        {"account_id": query},
                        {"ic_number": query},
                    ]
                },
                {
                    "_id": 0,
                    "customer_id": 1,
                    "name": 1,
                    "ic_number": 1,
                    "account_id": 1,
                    "segment": 1,
                    "address.state": 1,
                    "total_monthly_value_myr": 1,
                },
            )
            return [doc] if doc else []

    # --- Semantic ($vectorSearch) search ------------------------------
    #
    # Powered by the per-type AutoEmbed indexes on
    # `customers_residential` / `customers_commercial`. Atlas embeds the
    # query string in-cluster with the same Voyage model that indexed
    # `embed_source.text`, so callers pass the raw query text (not a
    # vector). The pipeline is identical across both collections, so we
    # build it once via `_build_vector_pipeline` and merge results from
    # both indexes when the caller doesn't pin a `customer_type` filter.

    @staticmethod
    def _build_vector_pipeline(
        query_text: str,
        *,
        index_name: str,
        limit: int,
        filter_: dict | None,
    ) -> list[dict]:
        """Compose the `$vectorSearch` aggregation for one customer index.

        Returns the raw stages so the route layer can surface them in
        the inspector envelope. The same `path` constant
        (`EMBED_SOURCE_PATH = "embed_source.text"`) is used by every
        AutoEmbed index in the system.
        """
        stage_vs: dict = {
            "$vectorSearch": {
                "index": index_name,
                "path": EMBED_SOURCE_PATH,
                "query": query_text,
                "numCandidates": max(limit * 30, 150),
                "limit": limit,
            }
        }
        if filter_:
            stage_vs["$vectorSearch"]["filter"] = filter_
        return [
            stage_vs,
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {
                "$project": {
                    "_id": 0,
                    "customer_id":    1,
                    "customer_type":  1,
                    "name":           1,
                    "tier":           1,
                    "entities":       1,
                    "address.state":  1,
                    "address.city":   1,
                    "embed_source":   1,
                    "score":          1,
                }
            },
        ]

    async def vector_search(
        self,
        query_text: str,
        *,
        limit: int = 20,
        customer_type: CustomerType | None = None,
        tier: str | None = None,
        entity: str | None = None,
        state: str | None = None,
    ) -> tuple[list[dict], list[dict[str, Any]], str]:
        """AutoEmbed-powered semantic customer search.

        Returns `(hits, pipelines, primary_collection)` so the route
        layer can render the executed pipeline in the inspector popup.
        `pipelines` is the list of `$vectorSearch` pipelines actually
        run (one per typed collection touched); when `customer_type` is
        unset we run both indexes and merge by score.

        Filter push-down: `tier`, `entity` (matched against the
        `entities` array), and `state` are pushed into the index filter
        when present so Atlas narrows candidates before scoring. The
        AutoEmbed index declarations in `scripts/setup_indexes.py` list
        each of these paths as `filter` fields so the predicates stay
        inside the vector index.
        """
        # --- Build the filter expression for the index --------------
        clauses: list[dict] = []
        if tier:
            clauses.append({"tier": tier})
        if entity:
            clauses.append({"entities": entity})
        if state:
            clauses.append({"address.state": state})
        # When the caller pins a customer_type we still attach it as an
        # explicit filter even though each AutoEmbed index lives on one
        # typed collection — Atlas treats the predicate as a no-op
        # there but the inspector payload reads cleaner.
        if customer_type:
            clauses.append({"customer_type": customer_type})
        filter_: dict | None
        if not clauses:
            filter_ = None
        elif len(clauses) == 1:
            filter_ = clauses[0]
        else:
            filter_ = {"$and": clauses}

        # --- Decide which indexes to query --------------------------
        targets: list[tuple[CustomerType, str, Any]] = []
        if customer_type == "residential":
            targets.append((
                "residential",
                IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
                self._coll_residential,
            ))
        elif customer_type == "commercial":
            targets.append((
                "commercial",
                IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
                self._coll_commercial,
            ))
        else:
            targets.append((
                "residential",
                IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED,
                self._coll_residential,
            ))
            targets.append((
                "commercial",
                IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED,
                self._coll_commercial,
            ))

        all_hits: list[dict] = []
        pipelines: list[dict[str, Any]] = []
        primary_collection = (
            CUSTOMERS_COMMERCIAL
            if customer_type == "commercial"
            else CUSTOMERS_RESIDENTIAL
        )

        for ctype, index_name, coll in targets:
            pipeline = self._build_vector_pipeline(
                query_text,
                index_name=index_name,
                limit=limit,
                filter_=filter_,
            )
            pipelines.append({
                "collection": coll.name,
                "index_name": index_name,
                "pipeline":   pipeline,
            })
            try:
                cursor = await coll.aggregate(pipeline)
                async for doc in cursor:
                    doc.setdefault("customer_type", ctype)
                    all_hits.append(doc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "customer_vector_search_failed",
                    collection=coll.name,
                    index=index_name,
                    error=str(exc),
                )

        # Merge & sort by descending vectorSearchScore, then trim.
        all_hits.sort(key=lambda d: d.get("score") or 0.0, reverse=True)
        return all_hits[:limit], pipelines, primary_collection

    async def page(
        self, *, skip: int, limit: int, customer_type: CustomerType | None = None
    ) -> list[dict]:
        """Paged listing.

        Defaults to residential when split is on (preserves the existing
        UX of the "All customers" page); pass `customer_type=` to scope.
        """
        ctype: CustomerType | None = customer_type
        if flags.STORAGE_SPLIT and ctype is None:
            ctype = "residential"
        coll = self._coll_for(ctype)
        cursor = coll.find(
            {},
            {
                "_id": 0,
                "customer_id": 1,
                "name": 1,
                "ic_number": 1,
                "account_id": 1,
                "segment": 1,
                "address.state": 1,
                "total_monthly_value_myr": 1,
            },
        ).sort([("name", 1)]).skip(skip).limit(limit)
        return [doc async for doc in cursor]

    # --- Writes -------------------------------------------------------

    async def upsert(
        self, doc: dict, *, customer_type: CustomerType | None = None
    ) -> int:
        """Type-aware upsert.

        - Without `customer_type`, infers from `doc["customer_type"]` or
          falls back to legacy.
        - When `STORAGE_SPLIT` is on, also upserts the
          `customer_index` row.
        """
        ctype: CustomerType | None = customer_type or doc.get("customer_type")
        coll = self._coll_for(ctype)
        result = await coll.replace_one(
            {"customer_id": doc["customer_id"]}, doc, upsert=True
        )
        if flags.STORAGE_SPLIT and ctype in ("residential", "commercial"):
            await self._coll_index.update_one(
                {"customer_id": doc["customer_id"]},
                {"$set": {
                    "customer_id":       doc["customer_id"],
                    "customer_type":     ctype,
                    "account_id":        doc.get("account_id"),
                    "parent_account_id": doc.get("parent_account_id"),
                    "name":              doc.get("name"),
                }},
                upsert=True,
            )
        return result.modified_count

    async def increment_lifetime_quarantine(
        self,
        customer_id: str,
        by: int = 1,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        result = await coll.update_one(
            {"customer_id": customer_id},
            {"$inc": {"lifetime_quarantine_count": by}},
        )
        return result.modified_count

    # --- PR-9 — Maintenance methods -----------------------------------
    #
    # These keep the RetailGroup-tier customer doc fresh as side-effecting
    # events flow through the platform (a new brand-journey milestone,
    # an opened support ticket, a marketing-campaign event, a status
    # change on an active campaign or a piece of equipment).
    #
    # All five mutate a single typed customer doc, bump
    # `updated_at`, and return `matched_count` so callers (workers and
    # routes alike) can detect a missing customer without paging in
    # the full document.

    async def push_brand_journey_event(
        self,
        customer_id: str,
        event: dict,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        """Append a brand-journey event. Full history is retained — no
        `$slice` (per spec, this is the audit-quality timeline)."""
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        result = await coll.update_one(
            {"customer_id": customer_id},
            {
                "$push": {"brand_journey": event},
                "$set": {"updated_at": _utcnow()},
            },
        )
        return result.matched_count

    async def push_support_interaction(
        self,
        customer_id: str,
        ticket: dict,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        """Append a support interaction; cap to the 20 most-recent."""
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        result = await coll.update_one(
            {"customer_id": customer_id},
            {
                "$push": {
                    "interaction_history.support_tickets": {
                        "$each": [ticket],
                        "$slice": -20,
                    }
                },
                "$set": {"updated_at": _utcnow()},
            },
        )
        return result.matched_count

    async def push_marketing_interaction(
        self,
        customer_id: str,
        event: dict,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        """Append a marketing event; cap to the 50 most-recent."""
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        result = await coll.update_one(
            {"customer_id": customer_id},
            {
                "$push": {
                    "interaction_history.marketing_events": {
                        "$each": [event],
                        "$slice": -50,
                    }
                },
                "$set": {"updated_at": _utcnow()},
            },
        )
        return result.matched_count

    async def set_active_campaign_status(
        self,
        customer_id: str,
        campaign_id: str,
        status: str,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        """Flip an `active_campaigns[].status` via positional array filters.

        Siblings are untouched. Sets `updated_at` on both the matched
        campaign element and the customer doc itself.
        """
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        now = _utcnow()
        result = await coll.update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "active_campaigns.$[c].status": status,
                    "active_campaigns.$[c].updated_at": now,
                    "updated_at": now,
                }
            },
            array_filters=[{"c.campaign_id": campaign_id}],
        )
        return result.matched_count

    async def set_equipment_status(
        self,
        customer_id: str,
        equipment_id: str,
        status: str,
        *,
        customer_type: CustomerType | None = None,
    ) -> int:
        """Flip an `equipment[].status` via positional array filters.

        Same idiom as `set_active_campaign_status`; siblings untouched.
        """
        ctype = customer_type or await self._resolve_type(customer_id)
        coll = self._coll_for(ctype)
        now = _utcnow()
        result = await coll.update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "equipment.$[e].status": status,
                    "equipment.$[e].updated_at": now,
                    "updated_at": now,
                }
            },
            array_filters=[{"e.equipment_id": equipment_id}],
        )
        return result.matched_count


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
