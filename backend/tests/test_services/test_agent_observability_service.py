"""Unit tests for `AgentObservabilityService` (PR-12 / G2).

Mocks `QuarantineCaseRepository` and `AiAssistService` so the suite
runs without Mongo or Bedrock. Covers:

  * `get_trace`
      - case missing → returns None
      - case exists, no agent_trace → has_trace False, empty arrays
      - case exists with trace → full payload + summary

  * `summarise_trace`
      - empty list
      - all-success entries
      - mix of errors (via `error` field and `status="error"`)
      - missing duration_ms collapses total to None

  * `batch_generate`
      - happy path counts generated/skipped
      - missing case is captured into errors
      - generic exceptions are captured into errors
      - `force` flag is forwarded to `AiAssistService.generate`
      - concurrency cap is honoured (max in-flight ≤ semaphore)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.errors import CaseNotFound
from app.services.agent_observability_service import (
    AgentObservabilityService,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trace_entry(
    *,
    node: str,
    duration_ms: float | None = 12.5,
    error: str | None = None,
    status: str | None = None,
) -> dict:
    """Build a trace entry shaped after `AgentTraceEntry`. `status` is
    not part of the canonical schema — we exercise the summariser's
    fallback handling for it."""
    started = _utcnow()
    entry: dict = {
        "node": node,
        "started_at": started,
        "finished_at": started,
        "output_keys": [],
        "metadata": {},
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if error is not None:
        entry["error"] = error
    if status is not None:
        entry["status"] = status
    return entry


# ---------------------------------------------------------------------------
# get_trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trace_returns_none_when_case_missing() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = None
    ai_assist = AsyncMock()

    svc = AgentObservabilityService(case_repo, ai_assist)
    out = await svc.get_trace("nope")
    assert out is None


@pytest.mark.asyncio
async def test_get_trace_case_without_agent_trace_returns_empty() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        "ai_assist": {"summary": "..."},  # no agent_trace key
    }
    ai_assist = AsyncMock()

    svc = AgentObservabilityService(case_repo, ai_assist)
    out = await svc.get_trace("case-1")

    assert out is not None
    assert out["case_id"] == "case-1"
    assert out["has_trace"] is False
    assert out["trace"] == []
    assert out["summary"]["path_taken"] == []
    assert out["summary"]["nodes_run"] == 0
    assert out["summary"]["error_count"] == 0
    assert out["summary"]["total_duration_ms"] is None


@pytest.mark.asyncio
async def test_get_trace_with_trace_returns_full_payload() -> None:
    trace = [
        _trace_entry(node="load_case", duration_ms=5.0),
        _trace_entry(node="classify", duration_ms=12.0),
        _trace_entry(node="retrieve", duration_ms=30.0),
    ]
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        "ai_assist": {"agent_trace": trace},
    }
    ai_assist = AsyncMock()

    svc = AgentObservabilityService(case_repo, ai_assist)
    out = await svc.get_trace("case-1")

    assert out is not None
    assert out["has_trace"] is True
    assert len(out["trace"]) == 3
    assert out["summary"]["path_taken"] == ["load_case", "classify", "retrieve"]
    assert out["summary"]["nodes_run"] == 3
    assert out["summary"]["error_count"] == 0
    assert out["summary"]["total_duration_ms"] == pytest.approx(47.0)


@pytest.mark.asyncio
async def test_get_trace_tolerates_non_list_agent_trace() -> None:
    """Legacy / malformed projection where `agent_trace` is not a list
    must collapse to has_trace=False rather than raise."""
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        "ai_assist": {"agent_trace": "not-a-list"},
    }
    ai_assist = AsyncMock()

    svc = AgentObservabilityService(case_repo, ai_assist)
    out = await svc.get_trace("case-1")

    assert out is not None
    assert out["has_trace"] is False
    assert out["trace"] == []


# ---------------------------------------------------------------------------
# summarise_trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarise_trace_empty() -> None:
    svc = AgentObservabilityService(AsyncMock(), AsyncMock())
    out = await svc.summarise_trace([])
    assert out == {
        "path_taken": [],
        "nodes_run": 0,
        "error_count": 0,
        "total_duration_ms": None,
    }


@pytest.mark.asyncio
async def test_summarise_trace_all_success() -> None:
    trace = [
        _trace_entry(node="load_case", duration_ms=2.0),
        _trace_entry(node="classify", duration_ms=4.0),
        _trace_entry(node="retrieve", duration_ms=8.0),
        _trace_entry(node="synthesise", duration_ms=16.0),
    ]
    svc = AgentObservabilityService(AsyncMock(), AsyncMock())
    out = await svc.summarise_trace(trace)
    assert out["path_taken"] == [
        "load_case", "classify", "retrieve", "synthesise"
    ]
    assert out["nodes_run"] == 4
    assert out["error_count"] == 0
    assert out["total_duration_ms"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_summarise_trace_with_errors() -> None:
    """Both `error="..."` and `status="error"` should count toward
    `error_count`. Repeated nodes don't double-count in `path_taken`."""
    trace = [
        _trace_entry(node="load_case", duration_ms=2.0),
        _trace_entry(node="classify", duration_ms=3.0, error="boom"),
        _trace_entry(node="retrieve", duration_ms=5.0, status="error"),
        _trace_entry(node="retrieve", duration_ms=4.0),  # retried; same node
    ]
    svc = AgentObservabilityService(AsyncMock(), AsyncMock())
    out = await svc.summarise_trace(trace)

    assert out["path_taken"] == ["load_case", "classify", "retrieve"]
    assert out["nodes_run"] == 3
    assert out["error_count"] == 2
    assert out["total_duration_ms"] == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_summarise_trace_missing_duration_collapses_total_to_none() -> None:
    trace = [
        _trace_entry(node="load_case", duration_ms=None),
        _trace_entry(node="classify", duration_ms=None),
    ]
    svc = AgentObservabilityService(AsyncMock(), AsyncMock())
    out = await svc.summarise_trace(trace)
    assert out["nodes_run"] == 2
    assert out["total_duration_ms"] is None


# ---------------------------------------------------------------------------
# batch_generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_generate_counts_generated_and_skipped() -> None:
    ai_assist = AsyncMock()
    # First call → fresh, second → cached, third → fresh.
    ai_assist.generate.side_effect = [
        {"case_id": "c1", "ai_assist": {}, "cached": False},
        {"case_id": "c2", "ai_assist": {}, "cached": True},
        {"case_id": "c3", "ai_assist": {}, "cached": False},
    ]

    svc = AgentObservabilityService(AsyncMock(), ai_assist)
    out = await svc.batch_generate(["c1", "c2", "c3"])

    assert out["requested"] == 3
    assert out["generated"] == 2
    assert out["skipped"] == 1
    assert out["errors"] == []
    assert ai_assist.generate.await_count == 3


@pytest.mark.asyncio
async def test_batch_generate_captures_case_not_found() -> None:
    ai_assist = AsyncMock()
    ai_assist.generate.side_effect = [
        {"case_id": "c1", "ai_assist": {}, "cached": False},
        CaseNotFound("c2"),
    ]

    svc = AgentObservabilityService(AsyncMock(), ai_assist)
    out = await svc.batch_generate(["c1", "c2"])

    assert out["generated"] == 1
    assert out["skipped"] == 0
    assert {"case_id": "c2", "reason": "case_not_found"} in out["errors"]


@pytest.mark.asyncio
async def test_batch_generate_captures_generic_exceptions() -> None:
    ai_assist = AsyncMock()
    ai_assist.generate.side_effect = [
        {"case_id": "c1", "ai_assist": {}, "cached": False},
        RuntimeError("bedrock 503"),
    ]

    svc = AgentObservabilityService(AsyncMock(), ai_assist)
    out = await svc.batch_generate(["c1", "c2"])

    assert out["generated"] == 1
    assert len(out["errors"]) == 1
    assert out["errors"][0]["case_id"] == "c2"
    assert "bedrock 503" in out["errors"][0]["reason"]


@pytest.mark.asyncio
async def test_batch_generate_forwards_force_flag() -> None:
    ai_assist = AsyncMock()
    ai_assist.generate.return_value = {
        "case_id": "c1", "ai_assist": {}, "cached": False
    }

    svc = AgentObservabilityService(AsyncMock(), ai_assist)
    await svc.batch_generate(["c1", "c2"], force=True)

    assert ai_assist.generate.await_count == 2
    for call in ai_assist.generate.await_args_list:
        assert call.kwargs["force"] is True


@pytest.mark.asyncio
async def test_batch_generate_respects_concurrency_cap() -> None:
    """Track concurrent in-flight calls; with `max_concurrency=2` the
    peak must never exceed 2 even with a 6-case batch."""
    in_flight = 0
    peak = 0

    async def fake_generate(case_id: str, *, force: bool = False):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Yield the loop so other tasks can be scheduled and we
        # actually observe contention rather than a serialised run.
        await asyncio.sleep(0.005)
        in_flight -= 1
        return {"case_id": case_id, "ai_assist": {}, "cached": False}

    ai_assist = AsyncMock()
    ai_assist.generate.side_effect = fake_generate

    svc = AgentObservabilityService(AsyncMock(), ai_assist)
    case_ids = [f"c{i}" for i in range(6)]
    out = await svc.batch_generate(case_ids, max_concurrency=2)

    assert out["requested"] == 6
    assert out["generated"] == 6
    assert peak <= 2
