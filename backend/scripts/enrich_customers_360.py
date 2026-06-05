"""PR-9 one-time migrator — enrich every existing customer to the
RetailGroup-tier 360 shape.

Walks `customers_residential` AND `customers_commercial` (plus the
legacy `customers` collection when STORAGE_SPLIT is off and the alias
points elsewhere) and, per row, fills in the rich fields the new 360
endpoint expects:

    unified_profile, entities, entity_profiles,
    cross_entity_metrics, brand_journey, interaction_history,
    active_campaigns, recommendations, equipment, current_cycle,
    embedding / embedding_text / embedding_generated_at

Existing values are preserved — the migrator only fills *missing* keys
and only writes the embedding once. A row is considered "already
enriched" when both `unified_profile` is present AND
`embedding_generated_at` is set; such rows are counted as `skipped`.

Mirrors `scripts/backfill_transactions_extref.py` /
`scripts/backfill_case_extref.py` / `scripts/reembed_history_corpus.py`
in shape: a `run_enrich(...)` core that tests can drive directly, and a
thin CLI `main()` that wraps it with `connect_mongo` /
`disconnect_mongo` and argparse.

Usage:
    python -m scripts.enrich_customers_360 --dry-run
    python -m scripts.enrich_customers_360 --batch-size 50
    python -m scripts.enrich_customers_360 --customer-type residential --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.constants import (
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from app.core.feature_flags import flags
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


# --- Field builders -----------------------------------------------------
# Each builder is pure: takes the legacy/lean customer doc + (optionally)
# its resolved customer_type, returns a dict suitable for $set when the
# corresponding top-level field is missing/empty. Builders never clobber
# existing data — that's enforced by the caller (`_compute_enrichment`).


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_unified_profile(doc: dict, ctype: str) -> dict:
    """Best-effort `unified_profile` from lean fields."""
    name = doc.get("customer_name") or doc.get("name") or "Customer"
    primary_contact = (
        doc.get("email")
        or doc.get("msisdn")
        or doc.get("phone")
        or None
    )
    segment = doc.get("customer_type") or ctype or "unknown"
    return {
        "display_name": name,
        "primary_contact": primary_contact,
        "segment": segment,
    }


def _build_entities(doc: dict) -> list[dict]:
    """Service-entity stubs derived from the lean `services` field.

    The lean schema sometimes carries a flat `services: [...]`; if it
    exists, project each row into a stub that the new 360 surface can
    later attach `entity_profiles` to. Otherwise return an empty list.
    """
    services = doc.get("services") or []
    if not isinstance(services, list):
        return []
    out: list[dict] = []
    for i, svc in enumerate(services):
        if isinstance(svc, dict):
            out.append({
                "entity_id": svc.get("entity_id") or svc.get("service_id") or f"entity_{i}",
                "entity_type": svc.get("entity_type") or svc.get("kind") or "service",
                "label": svc.get("label") or svc.get("name") or "",
            })
        else:
            out.append({
                "entity_id": f"entity_{i}",
                "entity_type": "service",
                "label": str(svc),
            })
    return out


def _default_cross_entity_metrics() -> dict:
    return {
        "ltv": {"total_myr": 0, "currency": "MYR"},
        "last_computed_at": _utcnow(),
    }


def _default_recommendations() -> dict:
    return {"nbo_offers": [], "churn_risk": None}


def _default_interaction_history() -> dict:
    return {
        "support_tickets": [],
        "marketing_events": [],
        "channel_engagement_rates": {},
    }


def _merge_interaction_history(existing: Any) -> dict:
    """Ensure the dict has all sub-keys without clobbering existing data."""
    base = _default_interaction_history()
    if isinstance(existing, dict):
        for k, v in existing.items():
            base[k] = v
        # Re-fill any sub-keys the merge dropped.
        if "support_tickets" not in base or not isinstance(base.get("support_tickets"), list):
            base["support_tickets"] = list(base.get("support_tickets") or [])
        if "marketing_events" not in base or not isinstance(base.get("marketing_events"), list):
            base["marketing_events"] = list(base.get("marketing_events") or [])
        if "channel_engagement_rates" not in base or not isinstance(
            base.get("channel_engagement_rates"), dict
        ):
            base["channel_engagement_rates"] = dict(base.get("channel_engagement_rates") or {})
    return base


def _is_missing(doc: dict, key: str) -> bool:
    """A field is "missing" if absent, None, or an empty container."""
    if key not in doc:
        return True
    val = doc[key]
    if val is None:
        return True
    if isinstance(val, (list, dict, str)) and len(val) == 0:
        return True
    return False


async def _maybe_compute_metrics(
    metrics_aggregator: Any, customer_id: str
) -> dict:
    """Call `MetricsAggregatorService.compute_for_customer` if available.

    The service is C1's deliverable; this script must work even when the
    binding hasn't landed yet (script-level imports must not fail). On
    any error we fall back to seeded defaults so the loop keeps moving.
    """
    if metrics_aggregator is None:
        return _default_cross_entity_metrics()
    fn = getattr(metrics_aggregator, "compute_for_customer", None)
    if fn is None:
        return _default_cross_entity_metrics()
    try:
        result = await fn(customer_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "enrich_metrics_compute_failed",
            customer_id=customer_id,
            error=str(exc),
        )
        return _default_cross_entity_metrics()
    if not isinstance(result, dict):
        return _default_cross_entity_metrics()
    return result


def _compute_enrichment(doc: dict, ctype: str) -> dict:
    """Build the `$set` payload for fields missing on `doc`.

    Pure / synchronous portion — callers add `cross_entity_metrics` and
    the embedding triple separately so async deps stay isolated.
    """
    updates: dict[str, Any] = {}
    if _is_missing(doc, "unified_profile"):
        updates["unified_profile"] = _build_unified_profile(doc, ctype)
    if "entities" not in doc:
        updates["entities"] = _build_entities(doc)
    if "entity_profiles" not in doc:
        updates["entity_profiles"] = {}
    if "brand_journey" not in doc:
        updates["brand_journey"] = list(doc.get("brand_journey") or [])
    # interaction_history is special — preserve sub-keys.
    if not isinstance(doc.get("interaction_history"), dict):
        updates["interaction_history"] = _default_interaction_history()
    else:
        merged = _merge_interaction_history(doc["interaction_history"])
        if merged != doc["interaction_history"]:
            updates["interaction_history"] = merged
    if "active_campaigns" not in doc:
        updates["active_campaigns"] = []
    if not isinstance(doc.get("recommendations"), dict):
        updates["recommendations"] = _default_recommendations()
    if "equipment" not in doc:
        updates["equipment"] = []
    if "current_cycle" not in doc:
        updates["current_cycle"] = {"last_recomputed_at": _utcnow()}
    return updates


def _is_already_enriched(doc: dict) -> bool:
    """Idempotency check.

    A row is "already enriched" when:
      - `unified_profile` exists (and is non-empty), AND
      - `embedding_generated_at` is set (truthy).
    """
    up = doc.get("unified_profile")
    if not isinstance(up, dict) or not up:
        return False
    return bool(doc.get("embedding_generated_at"))


def _resolved_collections(customer_type: str | None) -> list[str]:
    """Pick which collections the run should touch.

    - `--customer-type residential` → only the residential coll.
    - `--customer-type commercial`  → only the commercial coll.
    - default                       → both typed colls; if STORAGE_SPLIT
                                       is off AND the legacy `customers`
                                       alias resolves to a *different*
                                       physical collection, also
                                       include it.
    """
    if customer_type == "residential":
        return [CUSTOMERS_RESIDENTIAL]
    if customer_type == "commercial":
        return [CUSTOMERS_COMMERCIAL]
    out = [CUSTOMERS_RESIDENTIAL, CUSTOMERS_COMMERCIAL]
    if not flags.STORAGE_SPLIT and CUSTOMERS not in out:
        out.append(CUSTOMERS)
    return out


def _ctype_for_collection(coll_name: str) -> str:
    if coll_name == CUSTOMERS_COMMERCIAL:
        return "commercial"
    return "residential"


async def _embed_one(
    embedding_service: Any, merged_doc: dict
) -> dict | None:
    """Call `EmbeddingService.embed_customer(doc)` if available.

    Returns the `{embedding_text, embedding, embedding_generated_at}`
    triple or None when the service is unavailable / the call raises.
    """
    if embedding_service is None:
        return None
    fn = getattr(embedding_service, "embed_customer", None)
    if fn is None:
        return None
    try:
        result = await fn(merged_doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "enrich_embed_failed",
            customer_id=merged_doc.get("customer_id"),
            error=str(exc),
        )
        return None
    if not isinstance(result, dict):
        return None
    return result


async def run_enrich(
    *,
    db,
    embedding_service: Any,
    metrics_aggregator: Any,
    dry_run: bool = False,
    batch_size: int = 50,
    limit: int | None = None,
    customer_type: str | None = None,
) -> dict:
    """Core enrichment loop.

    Returns a stats dict: `{scanned, updated, skipped, errors}`.

    Tests target this directly with FakeDB + AsyncMocks so the embedding
    service / metrics aggregator never need to be real instances.
    """
    stats = {"scanned": 0, "updated": 0, "skipped": 0, "errors": 0}
    collections = _resolved_collections(customer_type)
    remaining = None if limit is None else int(limit)

    for coll_name in collections:
        if remaining is not None and remaining <= 0:
            break
        coll = db[coll_name]
        ctype = _ctype_for_collection(coll_name)
        cursor = coll.find({})
        async for doc in cursor:
            if remaining is not None and remaining <= 0:
                break
            stats["scanned"] += 1
            if remaining is not None:
                remaining -= 1

            try:
                if _is_already_enriched(doc):
                    stats["skipped"] += 1
                    continue

                # Phase 1: synchronous field fills.
                set_payload = _compute_enrichment(doc, ctype)

                # Phase 2: async cross-entity metrics — only if missing.
                if _is_missing(doc, "cross_entity_metrics"):
                    metrics = await _maybe_compute_metrics(
                        metrics_aggregator, doc.get("customer_id", "")
                    )
                    set_payload["cross_entity_metrics"] = metrics

                # Phase 3: embedding. Build a "merged" preview doc so the
                # embedding text reflects the freshly-filled rich fields.
                merged = {**doc, **set_payload}
                if not doc.get("embedding_generated_at") and not dry_run:
                    triple = await _embed_one(embedding_service, merged)
                    if triple is not None:
                        for k in ("embedding_text", "embedding", "embedding_generated_at"):
                            if k in triple:
                                set_payload[k] = triple[k]

                if not set_payload:
                    # Nothing to write — treat as skip (e.g. partial state
                    # already enriched but missing only embedding under
                    # --dry-run).
                    stats["skipped"] += 1
                    continue

                if not dry_run:
                    await coll.update_one(
                        {"customer_id": doc["customer_id"]},
                        {"$set": set_payload},
                    )
                stats["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.error(
                    "enrich_customer_failed",
                    customer_id=doc.get("customer_id"),
                    collection=coll_name,
                    error=str(exc),
                )

            # Periodic progress log per `batch_size` cohort.
            if batch_size and stats["scanned"] and stats["scanned"] % batch_size == 0:
                logger.info(
                    "enrich_customers_progress",
                    collection=coll_name,
                    **stats,
                )

    logger.info(
        "enrich_customers_360_done",
        dry_run=dry_run,
        batch_size=batch_size,
        limit=limit,
        customer_type=customer_type,
        **stats,
    )
    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="enrich_customers_360")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute updates but do not write or call embedding.")
    p.add_argument("--batch-size", type=int, default=50,
                   help="Progress-log cadence in scanned rows (default 50).")
    p.add_argument("--limit", type=int, default=None,
                   help="Optional cap on total customers scanned.")
    p.add_argument("--customer-type", choices=("residential", "commercial"),
                   default=None,
                   help="Restrict to a single typed collection.")
    return p.parse_args()


async def main() -> None:
    """CLI entry — wraps `run_enrich` with mongo + service plumbing."""
    from app.deps import connect_mongo, disconnect_mongo, get_db
    from app.services.embedding_service import EmbeddingService

    configure_logging()
    args = _parse_args()
    await connect_mongo()
    try:
        db = get_db()
        embedding_service = EmbeddingService()

        # Metrics aggregator is C1's deliverable. Best-effort import —
        # the script must remain runnable in environments where C1 has
        # not yet landed; the per-call fallback in
        # `_maybe_compute_metrics` then seeds defaults.
        metrics_aggregator: Any = None
        try:
            from app.services.metrics_aggregator_service import (  # type: ignore
                MetricsAggregatorService,
            )
            metrics_aggregator = MetricsAggregatorService(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("metrics_aggregator_unavailable", error=str(exc))

        stats = await run_enrich(
            db=db,
            embedding_service=embedding_service,
            metrics_aggregator=metrics_aggregator,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            customer_type=args.customer_type,
        )
        logger.info(
            "enrich_customers_360_summary",
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            customer_type=args.customer_type,
            **stats,
        )
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
