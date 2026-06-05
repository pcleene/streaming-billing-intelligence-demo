"""HTTP-level tests for `app.routes.metrics` (PR-12).

Uses FastAPI's `TestClient` with a hand-rolled `MetricsRefreshService`
stub injected via `app.dependency_overrides`. We don't need a real DB
or aggregator — the service contract is unit-tested separately in
`tests/test_services/test_metrics_refresh_service.py`. These tests
pin the HTTP shape (status codes, request/response bodies, query
param wiring).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.errors import CustomerNotFound
from app.main import create_app
from app.routes.metrics import _refresh_service
from app.services.metrics_refresh_service import MetricsRefreshService


def _build_client(stub: Any) -> TestClient:
    """Build a TestClient with the metrics service overridden.

    The lifespan is disabled because we have no live Mongo here.
    """
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.dependency_overrides[_refresh_service] = lambda: stub
    return TestClient(app)


def _make_stub_service(**kwargs: Any) -> AsyncMock:
    """Return an AsyncMock that quacks like MetricsRefreshService.

    `spec=` keeps test failures honest if the service grows new methods.
    """
    stub = AsyncMock(spec=MetricsRefreshService)
    if "refresh_one" in kwargs:
        stub.refresh_one = AsyncMock(side_effect=kwargs["refresh_one"])
    if "refresh_many" in kwargs:
        stub.refresh_many = AsyncMock(return_value=kwargs["refresh_many"])
    if "get_burst_status" in kwargs:
        stub.get_burst_status = AsyncMock(
            return_value=kwargs["get_burst_status"]
        )
    return stub


# ---------------------------------------------------------------------
# POST /api/customers/{customer_id}/metrics/refresh
# ---------------------------------------------------------------------


def test_refresh_one_happy_path_returns_200() -> None:
    payload = {
        "customer_id": "c-1",
        "computed": True,
        "skipped_reason": None,
        "cross_entity_metrics": {"ltv": {"total_myr": 100.0}},
    }
    stub = _make_stub_service(refresh_one=lambda *a, **k: payload)
    client = _build_client(stub)

    resp = client.post("/api/customers/c-1/metrics/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_id"] == "c-1"
    assert body["computed"] is True
    assert body["skipped_reason"] is None
    assert body["cross_entity_metrics"]["ltv"]["total_myr"] == 100.0
    stub.refresh_one.assert_awaited_once_with("c-1", force=False)


def test_refresh_one_force_flag_round_trips() -> None:
    payload = {
        "customer_id": "c-2",
        "computed": True,
        "skipped_reason": None,
        "cross_entity_metrics": {},
    }
    stub = _make_stub_service(refresh_one=lambda *a, **k: payload)
    client = _build_client(stub)

    resp = client.post("/api/customers/c-2/metrics/refresh?force=true")

    assert resp.status_code == 200
    stub.refresh_one.assert_awaited_once_with("c-2", force=True)


def test_refresh_one_cooldown_skip_surfaces_in_response() -> None:
    """When the service skips on cooldown, the route returns 200 with
    `computed=false` and `skipped_reason="cooldown_active"`."""
    payload = {
        "customer_id": "c-3",
        "computed": False,
        "skipped_reason": "cooldown_active",
        "cross_entity_metrics": {"ltv": {"total_myr": 50.0}},
    }
    stub = _make_stub_service(refresh_one=lambda *a, **k: payload)
    client = _build_client(stub)

    resp = client.post("/api/customers/c-3/metrics/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["computed"] is False
    assert body["skipped_reason"] == "cooldown_active"


def test_refresh_one_missing_customer_returns_404() -> None:
    """`CustomerNotFound` from the service maps to HTTP 404 via the
    AcmeError handler installed in `create_app`."""

    async def _raise(*_a, **_k):
        raise CustomerNotFound("missing")

    stub = AsyncMock(spec=MetricsRefreshService)
    stub.refresh_one = _raise
    client = _build_client(stub)

    resp = client.post("/api/customers/missing/metrics/refresh")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "CUSTOMER_NOT_FOUND"


# ---------------------------------------------------------------------
# POST /api/customers/metrics/batch-refresh
# ---------------------------------------------------------------------


def test_batch_refresh_happy_path_returns_200() -> None:
    stub = _make_stub_service(
        refresh_many={
            "requested": 3,
            "computed": 2,
            "skipped": 1,
            "errors": [],
        }
    )
    client = _build_client(stub)

    resp = client.post(
        "/api/customers/metrics/batch-refresh",
        json={"customer_ids": ["c-1", "c-2", "c-3"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "requested": 3,
        "computed": 2,
        "skipped": 1,
        "errors": [],
    }
    stub.refresh_many.assert_awaited_once_with(
        ["c-1", "c-2", "c-3"], force=False
    )


def test_batch_refresh_with_force_round_trips() -> None:
    stub = _make_stub_service(
        refresh_many={
            "requested": 1,
            "computed": 1,
            "skipped": 0,
            "errors": [],
        }
    )
    client = _build_client(stub)

    resp = client.post(
        "/api/customers/metrics/batch-refresh",
        json={"customer_ids": ["c-1"], "force": True},
    )

    assert resp.status_code == 200
    stub.refresh_many.assert_awaited_once_with(["c-1"], force=True)


def test_batch_refresh_over_50_ids_returns_422() -> None:
    """The `max_length` Pydantic validator caps the list at 50 ids."""
    stub = _make_stub_service(
        refresh_many={
            "requested": 0, "computed": 0, "skipped": 0, "errors": []
        }
    )
    client = _build_client(stub)

    resp = client.post(
        "/api/customers/metrics/batch-refresh",
        json={"customer_ids": [f"c-{i}" for i in range(51)]},
    )

    assert resp.status_code == 422
    # Service must NOT have been hit when validation rejects the body.
    stub.refresh_many.assert_not_awaited()


def test_batch_refresh_errors_array_passes_through() -> None:
    """Per-id errors from the service surface verbatim in the response."""
    stub = _make_stub_service(
        refresh_many={
            "requested": 2,
            "computed": 1,
            "skipped": 0,
            "errors": [{"customer_id": "missing", "reason": "not_found"}],
        }
    )
    client = _build_client(stub)

    resp = client.post(
        "/api/customers/metrics/batch-refresh",
        json={"customer_ids": ["ok", "missing"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == [
        {"customer_id": "missing", "reason": "not_found"}
    ]


# ---------------------------------------------------------------------
# GET /api/metrics/burst
# ---------------------------------------------------------------------


def test_burst_endpoint_happy_path_passes_through_envelope() -> None:
    envelope = {
        "run_id": "R",
        "active": True,
        "started_at": "2026-05-07T12:00:00+00:00",
        "ended_at": None,
        "rows": [],
        "samples": [{"observed_tps": 100.0}],
        "summary": {
            "row_count": 1,
            "sample_count": 1,
            "peak_tps": 100.0,
            "peak_observed_tps": 100.0,
        },
    }
    stub = _make_stub_service(get_burst_status=envelope)
    client = _build_client(stub)

    resp = client.get("/api/metrics/burst?run_id=R&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "R"
    assert body["active"] is True
    assert body["samples"][0]["observed_tps"] == 100.0
    stub.get_burst_status.assert_awaited_once_with(run_id="R", limit=10)


def test_burst_endpoint_default_limit_60_when_omitted() -> None:
    stub = _make_stub_service(
        get_burst_status={
            "run_id": None,
            "active": False,
            "rows": [],
            "samples": [],
            "summary": {"row_count": 0, "sample_count": 0},
        }
    )
    client = _build_client(stub)

    resp = client.get("/api/metrics/burst")

    assert resp.status_code == 200
    stub.get_burst_status.assert_awaited_once_with(run_id=None, limit=60)


def test_burst_endpoint_rejects_limit_above_720() -> None:
    """`limit` is bounded at 720; 721 must be rejected with 422."""
    stub = _make_stub_service(get_burst_status={"samples": []})
    client = _build_client(stub)

    resp = client.get("/api/metrics/burst?limit=721")

    assert resp.status_code == 422
    stub.get_burst_status.assert_not_awaited()
