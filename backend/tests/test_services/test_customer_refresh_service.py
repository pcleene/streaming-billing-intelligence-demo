"""Unit tests for `app.services.customer_refresh_service.CustomerRefreshService`.

Uses the in-memory `FakeDB` so we never talk to a real Mongo. Exercises:

- `refresh_one`: fresh path persists a stamp, cooldown gate skips,
  `force=True` bypasses the gate, missing customer returns the
  not_found envelope.
- `refresh_many`: all-fresh, mixed cooldown, per-id exceptions surface
  under `errors`, missing customers count as errors.
- `embedding_status`: no-embedding → stale, fresh → not stale,
  stale-by-age → stale, missing customer → None.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.repositories.customer_repo import CustomerRepository
from app.services.customer_360_service import Customer360Service
from app.services.customer_refresh_service import CustomerRefreshService
from tests._fakes import FakeDB


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _seed(
    db: FakeDB,
    *,
    customer_id: str,
    customer_type: str = "residential",
    cross_entity_metrics: dict | None = None,
    embedding: list | None = None,
    embedding_generated_at: datetime | None = None,
) -> None:
    coll = db["customers_residential"] if customer_type == "residential" else db["customers_commercial"]
    coll._docs.append(  # type: ignore[attr-defined]
        {
            "customer_id": customer_id,
            "customer_type": customer_type,
            "cross_entity_metrics": cross_entity_metrics or {},
            "embedding": embedding,
            "embedding_generated_at": embedding_generated_at,
            "current_cycle": {},
        }
    )


def _service(db: FakeDB, **kwargs) -> CustomerRefreshService:
    repo = CustomerRepository(db)
    return CustomerRefreshService(
        repo,
        Customer360Service(repo),
        **kwargs,
    )


# ---------------------------------------------------------------------
# refresh_one
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_one_fresh_persists_stamp() -> None:
    db = FakeDB()
    _seed(db, customer_id="c-fresh")
    svc = _service(db)

    out = await svc.refresh_one("c-fresh")

    assert out["customer_id"] == "c-fresh"
    assert out["refreshed"] is True
    assert out["skipped_reason"] is None
    assert isinstance(out["computed_at"], datetime)
    assert out["computed_at"].tzinfo is not None

    saved = await db["customers_residential"].find_one({"customer_id": "c-fresh"})
    assert isinstance(
        saved["cross_entity_metrics"]["last_computed_at"], datetime
    )
    assert isinstance(
        saved["current_cycle"]["last_recomputed_at"], datetime
    )


@pytest.mark.asyncio
async def test_refresh_one_cooldown_skips() -> None:
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed(
        db,
        customer_id="c-cool",
        cross_entity_metrics={"last_computed_at": recent},
    )
    svc = _service(db)

    out = await svc.refresh_one("c-cool")

    assert out["refreshed"] is False
    assert out["skipped_reason"] == "cooldown"
    # last_computed_at must NOT have been bumped.
    saved = await db["customers_residential"].find_one({"customer_id": "c-cool"})
    assert saved["cross_entity_metrics"]["last_computed_at"] == recent


@pytest.mark.asyncio
async def test_refresh_one_force_bypasses_cooldown() -> None:
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed(
        db,
        customer_id="c-cool",
        cross_entity_metrics={"last_computed_at": recent},
    )
    svc = _service(db)

    out = await svc.refresh_one("c-cool", force=True)

    assert out["refreshed"] is True
    assert out["skipped_reason"] is None
    saved = await db["customers_residential"].find_one({"customer_id": "c-cool"})
    assert saved["cross_entity_metrics"]["last_computed_at"] > recent


@pytest.mark.asyncio
async def test_refresh_one_missing_customer_returns_not_found_envelope() -> None:
    db = FakeDB()
    svc = _service(db)

    out = await svc.refresh_one("c-missing")

    assert out["refreshed"] is False
    assert out["skipped_reason"] == "not_found"


# ---------------------------------------------------------------------
# refresh_many
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_many_concurrency_and_counters() -> None:
    db = FakeDB()
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed(db, customer_id="c-1")
    _seed(db, customer_id="c-2")
    _seed(
        db,
        customer_id="c-cool",
        cross_entity_metrics={"last_computed_at": recent},
    )
    svc = _service(db)

    out = await svc.refresh_many(["c-1", "c-2", "c-cool"], max_concurrency=2)

    assert out == {
        "requested": 3,
        "refreshed": 2,
        "skipped": 1,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_refresh_many_missing_id_recorded_as_error() -> None:
    db = FakeDB()
    _seed(db, customer_id="c-1")
    svc = _service(db)

    out = await svc.refresh_many(["c-1", "c-missing"])

    assert out["requested"] == 2
    assert out["refreshed"] == 1
    assert out["skipped"] == 0
    assert out["errors"] == [
        {"customer_id": "c-missing", "reason": "not_found"}
    ]


@pytest.mark.asyncio
async def test_refresh_many_per_id_exception_captured(monkeypatch) -> None:
    db = FakeDB()
    _seed(db, customer_id="c-good")
    _seed(db, customer_id="c-bad")
    svc = _service(db)

    real = svc.refresh_one

    async def _bomb(cid: str, *, force: bool = False):
        if cid == "c-bad":
            raise RuntimeError("boom")
        return await real(cid, force=force)

    monkeypatch.setattr(svc, "refresh_one", _bomb)

    out = await svc.refresh_many(["c-good", "c-bad"])

    assert out["refreshed"] == 1
    assert {e["customer_id"] for e in out["errors"]} == {"c-bad"}
    assert "boom" in out["errors"][0]["reason"]


# ---------------------------------------------------------------------
# embedding_status
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_status_fresh() -> None:
    db = FakeDB()
    fresh = datetime.now(timezone.utc) - timedelta(days=1)
    _seed(
        db,
        customer_id="c-emb",
        embedding=[0.1] * 1024,
        embedding_generated_at=fresh,
    )
    svc = _service(db)

    out = await svc.embedding_status("c-emb")

    assert out is not None
    assert out["has_embedding"] is True
    assert out["dim"] == 1024
    assert out["is_stale"] is False
    assert out["age_seconds"] is not None and out["age_seconds"] > 0
    assert out["generated_at"] == fresh


@pytest.mark.asyncio
async def test_embedding_status_stale_by_age() -> None:
    db = FakeDB()
    old = datetime.now(timezone.utc) - timedelta(days=45)
    _seed(
        db,
        customer_id="c-old",
        embedding=[0.1] * 8,
        embedding_generated_at=old,
    )
    svc = _service(db)

    out = await svc.embedding_status("c-old")

    assert out is not None
    assert out["has_embedding"] is True
    assert out["is_stale"] is True


@pytest.mark.asyncio
async def test_embedding_status_no_embedding_is_stale() -> None:
    db = FakeDB()
    _seed(db, customer_id="c-noemb")
    svc = _service(db)

    out = await svc.embedding_status("c-noemb")

    assert out is not None
    assert out["has_embedding"] is False
    assert out["dim"] is None
    assert out["age_seconds"] is None
    assert out["is_stale"] is True


@pytest.mark.asyncio
async def test_embedding_status_missing_customer_returns_none() -> None:
    db = FakeDB()
    svc = _service(db)

    assert await svc.embedding_status("c-nope") is None


# ---------------------------------------------------------------------
# AsyncMock interplay (tiny sanity check that the service composes
# cleanly with mock-based dependencies the way the agent path does).
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_one_invokes_repo() -> None:
    repo = AsyncMock()
    repo.get_by_customer_id = AsyncMock(return_value=None)
    profile = Customer360Service(repo)
    svc = CustomerRefreshService(repo, profile)
    out = await svc.refresh_one("anybody")
    assert out["skipped_reason"] == "not_found"
    repo.get_by_customer_id.assert_awaited_once_with("anybody")
