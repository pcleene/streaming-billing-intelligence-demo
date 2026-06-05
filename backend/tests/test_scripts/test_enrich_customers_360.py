"""Unit tests for `scripts/enrich_customers_360.py`.

Tests drive `run_enrich` directly with a FakeDB + AsyncMocked embedding /
metrics services, so neither Voyage nor Mongo nor C1's
`MetricsAggregatorService` need to exist for these tests to pass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.constants import (
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
)
from scripts.enrich_customers_360 import run_enrich
from tests._fakes import FakeDB


# --- Helpers ------------------------------------------------------------


def _seed_customer(db: FakeDB, coll_name: str, cid: str, *,
                   ctype: str = "residential", services: list | None = None) -> dict:
    """Insert a lean customer row and return the doc reference."""
    doc = {
        "customer_id": cid,
        "name": f"Customer {cid}",
        "email": f"{cid}@example.com",
        "customer_type": ctype,
        "services": list(services or []),
    }
    db[coll_name]._docs.append(doc)
    return doc


def _seed_mixed(db: FakeDB, *, residential: int, commercial: int) -> None:
    for i in range(residential):
        _seed_customer(db, CUSTOMERS_RESIDENTIAL, f"r_{i}", ctype="residential",
                       services=[{"service_id": f"svc_{i}", "name": "Acme Premium"}])
    for j in range(commercial):
        _seed_customer(db, CUSTOMERS_COMMERCIAL, f"c_{j}", ctype="commercial")


def _make_embed_service() -> AsyncMock:
    """Stand-in for `EmbeddingService.embed_customer`.

    The script calls `svc.embed_customer(merged_doc)` and expects a dict
    with embedding / embedding_text / embedding_generated_at.
    """
    async def _embed_customer(doc):
        return {
            "embedding_text": f"embed::{doc.get('customer_id', '?')}",
            "embedding": [0.1] * 1024,
            "embedding_generated_at": datetime.now(timezone.utc),
        }

    svc = AsyncMock()
    svc.embed_customer = AsyncMock(side_effect=_embed_customer)
    return svc


def _make_metrics_service() -> AsyncMock:
    async def _compute(customer_id: str):
        return {
            "ltv": {"total_myr": 42.0, "currency": "MYR"},
            "last_computed_at": datetime.now(timezone.utc),
            "_computed_for": customer_id,
        }

    svc = AsyncMock()
    svc.compute_for_customer = AsyncMock(side_effect=_compute)
    return svc


# --- Tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_fills_all_rich_fields_for_mixed_customers():
    """Seed 10 lean customers (mix of residential + commercial). After
    `run_enrich` every row carries unified_profile, cross_entity_metrics,
    embedding, and embedding_generated_at."""
    db = FakeDB()
    _seed_mixed(db, residential=6, commercial=4)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type=None,
    )

    assert stats["scanned"] == 10
    assert stats["updated"] == 10
    assert stats["skipped"] == 0
    assert stats["errors"] == 0

    all_docs = (
        db[CUSTOMERS_RESIDENTIAL]._docs
        + db[CUSTOMERS_COMMERCIAL]._docs
    )
    assert len(all_docs) == 10
    for d in all_docs:
        assert isinstance(d.get("unified_profile"), dict)
        assert d["unified_profile"]["display_name"]
        assert d["unified_profile"]["primary_contact"] == d["email"]
        assert isinstance(d.get("cross_entity_metrics"), dict)
        assert "ltv" in d["cross_entity_metrics"]
        assert isinstance(d.get("embedding"), list)
        assert len(d["embedding"]) == 1024
        assert d.get("embedding_generated_at") is not None
        # Other rich fields seeded
        assert "entities" in d
        assert isinstance(d.get("entity_profiles"), dict)
        assert d.get("active_campaigns") == []
        assert d.get("equipment") == []
        assert isinstance(d.get("interaction_history"), dict)
        ih = d["interaction_history"]
        assert ih["support_tickets"] == []
        assert ih["marketing_events"] == []
        assert ih["channel_engagement_rates"] == {}
        assert isinstance(d.get("recommendations"), dict)
        assert d["recommendations"]["churn_risk"] is None


@pytest.mark.asyncio
async def test_enrich_is_idempotent_on_already_enriched():
    """Re-running on already-enriched rows yields skipped == N, updated == 0."""
    db = FakeDB()
    _seed_mixed(db, residential=6, commercial=4)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    # First run — full enrichment.
    first = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type=None,
    )
    assert first["updated"] == 10

    # Reset call counts so the second run's expectations are crisp.
    embed.embed_customer.reset_mock()
    metrics.compute_for_customer.reset_mock()

    # Second run — every row has unified_profile + embedding_generated_at,
    # so all 10 must be skipped without any service calls.
    second = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type=None,
    )
    assert second["scanned"] == 10
    assert second["skipped"] == 10
    assert second["updated"] == 0
    assert second["errors"] == 0
    # Idempotency must NOT round-trip to embedding service or metrics.
    assert embed.embed_customer.await_count == 0
    assert metrics.compute_for_customer.await_count == 0


@pytest.mark.asyncio
async def test_enrich_dry_run_skips_writes_and_embedding_call():
    """`--dry-run` must not write to the DB and must not call the
    embedding service. Counters still report what *would* happen."""
    db = FakeDB()
    _seed_mixed(db, residential=2, commercial=1)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    # Snapshot pre-state.
    before_res = [dict(d) for d in db[CUSTOMERS_RESIDENTIAL]._docs]
    before_com = [dict(d) for d in db[CUSTOMERS_COMMERCIAL]._docs]

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=True,
        batch_size=50,
        limit=None,
        customer_type=None,
    )

    assert stats["scanned"] == 3
    assert stats["updated"] == 3
    assert stats["errors"] == 0
    # Embedding service must NOT be invoked under --dry-run (per spec).
    assert embed.embed_customer.await_count == 0
    # DB rows must be byte-identical to pre-state.
    assert db[CUSTOMERS_RESIDENTIAL]._docs == before_res
    assert db[CUSTOMERS_COMMERCIAL]._docs == before_com


@pytest.mark.asyncio
async def test_enrich_respects_limit():
    """`--limit=3` processes only 3 customers across the typed collections."""
    db = FakeDB()
    _seed_mixed(db, residential=5, commercial=5)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=3,
        customer_type=None,
    )

    assert stats["scanned"] == 3
    assert stats["updated"] == 3
    # Only 3 customers should have an embedding now.
    enriched = [
        d for d in db[CUSTOMERS_RESIDENTIAL]._docs + db[CUSTOMERS_COMMERCIAL]._docs
        if d.get("embedding_generated_at") is not None
    ]
    assert len(enriched) == 3


@pytest.mark.asyncio
async def test_enrich_customer_type_residential_only_touches_residential():
    """`--customer-type=residential` leaves the commercial collection alone."""
    db = FakeDB()
    _seed_mixed(db, residential=4, commercial=3)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type="residential",
    )

    assert stats["scanned"] == 4
    assert stats["updated"] == 4
    # Residential rows enriched.
    for d in db[CUSTOMERS_RESIDENTIAL]._docs:
        assert d.get("embedding_generated_at") is not None
        assert d["unified_profile"]["segment"] == "residential"
    # Commercial rows untouched.
    for d in db[CUSTOMERS_COMMERCIAL]._docs:
        assert "unified_profile" not in d
        assert "embedding" not in d


@pytest.mark.asyncio
async def test_enrich_customer_type_commercial_only_touches_commercial():
    db = FakeDB()
    _seed_mixed(db, residential=2, commercial=3)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type="commercial",
    )

    assert stats["scanned"] == 3
    assert stats["updated"] == 3
    for d in db[CUSTOMERS_COMMERCIAL]._docs:
        assert d.get("embedding_generated_at") is not None
        assert d["unified_profile"]["segment"] == "commercial"
    for d in db[CUSTOMERS_RESIDENTIAL]._docs:
        assert "unified_profile" not in d


@pytest.mark.asyncio
async def test_enrich_calls_embedding_service_per_customer_with_5_rows_batch_2():
    """`--batch-size=2` with 5 customers — embedding is called per
    customer (not batched across customers in this script), so we
    simply assert scanned == 5 and embed_customer.await_count == 5."""
    db = FakeDB()
    _seed_mixed(db, residential=3, commercial=2)
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=2,
        limit=None,
        customer_type=None,
    )

    assert stats["scanned"] == 5
    assert stats["updated"] == 5
    # Per-customer embedding call (5 customers → 5 calls).
    assert embed.embed_customer.await_count == 5
    assert metrics.compute_for_customer.await_count == 5


@pytest.mark.asyncio
async def test_enrich_works_when_metrics_aggregator_missing():
    """C1 may not have landed yet — `metrics_aggregator=None` is valid
    and the loop falls back to seeded defaults rather than erroring."""
    db = FakeDB()
    _seed_mixed(db, residential=2, commercial=0)
    embed = _make_embed_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=None,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type=None,
    )

    assert stats["scanned"] == 2
    assert stats["updated"] == 2
    assert stats["errors"] == 0
    for d in db[CUSTOMERS_RESIDENTIAL]._docs:
        # Default seed payload from _default_cross_entity_metrics().
        assert d["cross_entity_metrics"]["ltv"]["currency"] == "MYR"
        assert d["cross_entity_metrics"]["ltv"]["total_myr"] == 0


@pytest.mark.asyncio
async def test_enrich_preserves_existing_rich_fields():
    """The migrator must NEVER clobber an existing field; only fill gaps."""
    db = FakeDB()
    pre_brand = [{"step": "joined", "at": "2024-01-01"}]
    pre_recs = {"nbo_offers": [{"offer_id": "o1"}], "churn_risk": "low"}
    db[CUSTOMERS_RESIDENTIAL]._docs.append({
        "customer_id": "r_keep",
        "name": "Keepy",
        "email": "k@example.com",
        "customer_type": "residential",
        "brand_journey": pre_brand,
        "recommendations": pre_recs,
        "interaction_history": {
            "support_tickets": [{"ticket_id": "t1"}],
            # marketing_events / channel_engagement_rates intentionally absent
        },
    })
    embed = _make_embed_service()
    metrics = _make_metrics_service()

    stats = await run_enrich(
        db=db,
        embedding_service=embed,
        metrics_aggregator=metrics,
        dry_run=False,
        batch_size=50,
        limit=None,
        customer_type="residential",
    )
    assert stats["updated"] == 1
    d = db[CUSTOMERS_RESIDENTIAL]._docs[0]
    # Pre-existing brand_journey kept verbatim.
    assert d["brand_journey"] == pre_brand
    # Pre-existing recommendations kept verbatim.
    assert d["recommendations"] == pre_recs
    # interaction_history existing sub-keys preserved, missing ones filled.
    assert d["interaction_history"]["support_tickets"] == [{"ticket_id": "t1"}]
    assert d["interaction_history"]["marketing_events"] == []
    assert d["interaction_history"]["channel_engagement_rates"] == {}
