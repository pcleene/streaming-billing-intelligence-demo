"""Route tests for the agent-observability surface (PR-12 / G2).

Drives FastAPI's `TestClient` against `create_app()` and overrides the
`_agent_observability_service` provider so the route layer is exercised
in isolation from Mongo / Bedrock.

Covers:
  * GET /api/quarantine/cases/{case_id}/ai-assist/trace
      - happy path returns the full {case_id, has_trace, trace, summary}
      - 404 when the case doesn't exist
      - has_trace=false / empty arrays when the case exists but no run
  * POST /api/quarantine/cases/batch-ai-assist
      - happy-path counts
      - 422 when more than 25 ids are sent
      - 422 when the list is empty
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routes import quarantine as quarantine_route


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def client_and_svc():
    """Spin up the app with a stubbed observability service.

    Yields ``(client, svc_mock)`` so each test can configure the
    AsyncMock's return values for the case under test. Cleans up the
    dependency override afterwards so test ordering doesn't leak
    state between cases.
    """
    app = create_app()
    # Avoid the Mongo lifespan in unit tests.
    app.router.lifespan_context = None  # type: ignore[assignment]

    svc_mock = AsyncMock()
    app.dependency_overrides[
        quarantine_route._agent_observability_service
    ] = lambda: svc_mock

    client = TestClient(app)
    try:
        yield client, svc_mock
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/quarantine/cases/{case_id}/ai-assist/trace
# ---------------------------------------------------------------------------


def test_get_trace_happy_path(client_and_svc) -> None:
    client, svc = client_and_svc
    svc.get_trace.return_value = {
        "case_id": "case-1",
        "has_trace": True,
        "trace": [
            {
                "node": "load_case",
                "started_at": _utcnow_iso(),
                "finished_at": _utcnow_iso(),
                "duration_ms": 5.0,
                "output_keys": ["case"],
                "error": None,
                "metadata": {},
            },
            {
                "node": "classify",
                "started_at": _utcnow_iso(),
                "finished_at": _utcnow_iso(),
                "duration_ms": 12.0,
                "output_keys": ["classification"],
                "error": None,
                "metadata": {"recommended_path": "fast"},
            },
        ],
        "summary": {
            "path_taken": ["load_case", "classify"],
            "nodes_run": 2,
            "error_count": 0,
            "total_duration_ms": 17.0,
        },
    }

    resp = client.get("/api/quarantine/cases/case-1/ai-assist/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == "case-1"
    assert body["has_trace"] is True
    assert len(body["trace"]) == 2
    assert body["summary"]["path_taken"] == ["load_case", "classify"]
    assert body["summary"]["nodes_run"] == 2
    assert body["summary"]["error_count"] == 0
    assert body["summary"]["total_duration_ms"] == 17.0
    svc.get_trace.assert_awaited_once_with("case-1")


def test_get_trace_404_when_case_missing(client_and_svc) -> None:
    client, svc = client_and_svc
    svc.get_trace.return_value = None

    resp = client.get("/api/quarantine/cases/case-missing/ai-assist/trace")
    assert resp.status_code == 404
    body = resp.json()
    # AcmeError handler shape: {"error": {"code": ..., "message": ...}}
    assert body["error"]["code"] == "CASE_NOT_FOUND"
    assert "case-missing" in body["error"]["message"]


def test_get_trace_has_trace_false_when_no_run_yet(client_and_svc) -> None:
    client, svc = client_and_svc
    svc.get_trace.return_value = {
        "case_id": "case-2",
        "has_trace": False,
        "trace": [],
        "summary": {
            "path_taken": [],
            "nodes_run": 0,
            "error_count": 0,
            "total_duration_ms": None,
        },
    }

    resp = client.get("/api/quarantine/cases/case-2/ai-assist/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_trace"] is False
    assert body["trace"] == []
    assert body["summary"]["nodes_run"] == 0
    assert body["summary"]["total_duration_ms"] is None


# ---------------------------------------------------------------------------
# POST /api/quarantine/cases/batch-ai-assist
# ---------------------------------------------------------------------------


def test_batch_ai_assist_happy_path(client_and_svc) -> None:
    client, svc = client_and_svc
    svc.batch_generate.return_value = {
        "requested": 3,
        "generated": 2,
        "skipped": 1,
        "errors": [],
    }

    resp = client.post(
        "/api/quarantine/cases/batch-ai-assist",
        json={"case_ids": ["c1", "c2", "c3"], "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "requested": 3,
        "generated": 2,
        "skipped": 1,
        "errors": [],
    }
    svc.batch_generate.assert_awaited_once()
    args, kwargs = svc.batch_generate.call_args
    assert args[0] == ["c1", "c2", "c3"]
    assert kwargs["force"] is False


def test_batch_ai_assist_rejects_more_than_25_ids(client_and_svc) -> None:
    client, svc = client_and_svc
    too_many = [f"c{i}" for i in range(26)]
    resp = client.post(
        "/api/quarantine/cases/batch-ai-assist",
        json={"case_ids": too_many},
    )
    assert resp.status_code == 422
    # Service must NOT be hit when validation fails.
    svc.batch_generate.assert_not_awaited()


def test_batch_ai_assist_rejects_empty_list(client_and_svc) -> None:
    client, svc = client_and_svc
    resp = client.post(
        "/api/quarantine/cases/batch-ai-assist",
        json={"case_ids": []},
    )
    assert resp.status_code == 422
    svc.batch_generate.assert_not_awaited()


def test_batch_ai_assist_forwards_force_flag(client_and_svc) -> None:
    client, svc = client_and_svc
    svc.batch_generate.return_value = {
        "requested": 1, "generated": 1, "skipped": 0, "errors": []
    }
    resp = client.post(
        "/api/quarantine/cases/batch-ai-assist",
        json={"case_ids": ["c1"], "force": True},
    )
    assert resp.status_code == 200
    args, kwargs = svc.batch_generate.call_args
    assert kwargs["force"] is True
