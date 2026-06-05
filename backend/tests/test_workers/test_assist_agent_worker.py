"""Unit tests for `app.workers.assist_agent_worker` (PR-AG)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.assist_agent_worker import (
    AssistAgentRunSummary,
    _AgentWorker,
    _summarise_state,
)


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class _FakeStream:
    """Minimal async-iterable change stream stub.

    `events` is a list of pre-built change docs. After we exhaust the
    list we return `asyncio.TimeoutError` on subsequent `next()` calls
    so the worker's wait_for loop keeps spinning until shutdown — this
    matches the real change stream's behaviour during quiet windows.
    """

    def __init__(self, events: list[dict]) -> None:
        self._events = list(events)
        self._idx = 0

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *_a) -> None:  # noqa: D401 — context-manager API
        return None

    async def next(self) -> dict:
        if self._idx >= len(self._events):
            # Mimic an idle change stream: wait_for in the worker will
            # surface this as a TimeoutError and continue looping.
            await asyncio.sleep(10)
            raise asyncio.TimeoutError
        ev = self._events[self._idx]
        self._idx += 1
        return ev


class _FakeColl:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream
        self.watch_calls: list[dict] = []

    def watch(self, *, pipeline=None, full_document=None):  # noqa: D401
        self.watch_calls.append(
            {"pipeline": pipeline, "full_document": full_document}
        )
        return self._stream


class _FakeDB:
    def __init__(self, coll: _FakeColl) -> None:
        self._coll = coll

    def __getitem__(self, name: str) -> _FakeColl:
        return self._coll


def _insert_event(case_id: str) -> dict:
    return {
        "operationType": "insert",
        "fullDocument": {"case_id": case_id, "status": "open"},
    }


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_disabled_when_flag_off() -> None:
    """`enabled=False` must skip the change-stream loop entirely and
    leave the agent untouched."""
    agent = AsyncMock()
    fake_db = _FakeDB(_FakeColl(_FakeStream([])))

    worker = _AgentWorker(db=fake_db, agent=agent, enabled=False)
    await asyncio.wait_for(worker.run(), timeout=1.0)

    agent.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_dispatches_agent_on_insert_event() -> None:
    """One insert event → one `agent.run(case_id=...)` await."""
    agent = AsyncMock()
    agent.run = AsyncMock(
        return_value={"trace": [], "errors": [], "synthesis": {}}
    )
    stream = _FakeStream([_insert_event("case-42")])
    fake_db = _FakeDB(_FakeColl(stream))

    worker = _AgentWorker(db=fake_db, agent=agent, enabled=True)
    task = asyncio.create_task(worker.run())
    # Give the event loop time to consume the event + dispatch + finish.
    await asyncio.sleep(0.1)
    await worker.shutdown()
    await asyncio.wait_for(task, timeout=2.0)

    agent.run.assert_awaited_once_with(case_id="case-42")


@pytest.mark.asyncio
async def test_worker_handles_per_case_failure_without_killing_loop() -> None:
    """A raise on the first event must not stop later events from
    being dispatched."""
    seen: list[str] = []

    async def _flaky_run(*, case_id: str) -> dict:
        seen.append(case_id)
        if case_id == "case-bad":
            raise RuntimeError("agent blew up")
        return {"trace": [], "errors": [], "synthesis": {}}

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=_flaky_run)
    stream = _FakeStream([_insert_event("case-bad"), _insert_event("case-good")])
    fake_db = _FakeDB(_FakeColl(stream))

    worker = _AgentWorker(db=fake_db, agent=agent, enabled=True)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.15)
    await worker.shutdown()
    await asyncio.wait_for(task, timeout=2.0)

    assert seen == ["case-bad", "case-good"]
    assert agent.run.await_count == 2


@pytest.mark.asyncio
async def test_worker_bounds_concurrency_by_semaphore() -> None:
    """With max_concurrent=2 and 5 events, no more than 2 in-flight
    agent.run calls at any time."""
    release = asyncio.Event()
    inflight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _holding_run(*, case_id: str) -> dict:  # noqa: ARG001
        nonlocal inflight, peak
        async with lock:
            inflight += 1
            if inflight > peak:
                peak = inflight
        # Hold until the test releases — exposes any over-fan-out.
        await release.wait()
        async with lock:
            inflight -= 1
        return {"trace": [], "errors": [], "synthesis": {}}

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=_holding_run)
    events = [_insert_event(f"c-{i}") for i in range(5)]
    stream = _FakeStream(events)
    fake_db = _FakeDB(_FakeColl(stream))

    worker = _AgentWorker(
        db=fake_db, agent=agent, enabled=True, max_concurrent=2
    )
    task = asyncio.create_task(worker.run())
    # Wait long enough for the worker to consume all events and try to
    # dispatch them. If the semaphore weren't bounding we'd see peak=5.
    await asyncio.sleep(0.3)
    assert peak <= 2, f"semaphore breach: peak inflight = {peak}"
    # Release everyone and let the worker drain.
    release.set()
    await asyncio.sleep(0.1)
    await worker.shutdown()
    await asyncio.wait_for(task, timeout=2.0)

    assert agent.run.await_count == 5


def test_summarise_extracts_path_taken_deep() -> None:
    """A trace containing an analytics entry must map to path_taken=deep."""
    state = {
        "trace": [
            {"node": "load_case", "duration_ms": 1.0, "status": "ok"},
            {"node": "classify", "duration_ms": 2.0, "status": "ok"},
            {"node": "analytics", "duration_ms": 7.5, "status": "ok"},
            {"node": "vector_search", "duration_ms": 4.0, "status": "ok"},
            {"node": "synthesize", "duration_ms": 1.5, "status": "ok"},
            {"node": "persist", "duration_ms": 0.5, "status": "ok"},
        ],
        "errors": [],
        "synthesis": {"degraded": False},
    }
    summary = _summarise_state("c-1", state)
    assert summary.path_taken == "deep"
    assert summary.nodes_run == 6
    assert summary.error_count == 0
    assert summary.degraded is False
    assert summary.total_duration_ms == pytest.approx(16.5)


def test_summarise_extracts_path_taken_fast() -> None:
    """A trace without an analytics entry must map to path_taken=fast."""
    state = {
        "trace": [
            {"node": "load_case", "duration_ms": 1.0, "status": "ok"},
            {"node": "classify", "duration_ms": 2.0, "status": "ok"},
            {"node": "vector_search", "duration_ms": 4.0, "status": "ok"},
            {"node": "synthesize", "duration_ms": 1.5, "status": "ok"},
            {"node": "persist", "duration_ms": 0.5, "status": "ok"},
        ],
        "errors": ["x: y"],
        "synthesis": {"degraded": True},
    }
    summary = _summarise_state("c-2", state)
    assert summary.path_taken == "fast"
    assert summary.nodes_run == 5
    assert summary.error_count == 1
    assert summary.degraded is True


def test_summary_is_pydantic_serialisable() -> None:
    """The summary doubles as a structured-log payload via
    `model_dump`. Sanity-check the shape so accidental field changes
    surface here."""
    summary = AssistAgentRunSummary(
        case_id="c", path_taken="fast", nodes_run=1
    )
    dumped = summary.model_dump()
    assert dumped["case_id"] == "c"
    assert dumped["path_taken"] == "fast"
    assert dumped["nodes_run"] == 1


@pytest.mark.asyncio
async def test_main_short_circuits_when_flag_off() -> None:
    """When `flags.AI_ASSIST_AGENTIC` is False, `main()` must NOT build
    the agent or open a change stream."""
    with (
        patch(
            "app.workers.assist_agent_worker.connect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.assist_agent_worker.disconnect_mongo",
            new=AsyncMock(),
        ),
        patch(
            "app.workers.assist_agent_worker.get_db",
            return_value=MagicMock(),
        ),
        patch(
            "app.workers.assist_agent_worker._build_agent"
        ) as build_agent,
        patch("app.workers.assist_agent_worker.configure_logging"),
        patch(
            "app.core.feature_flags.flags.AI_ASSIST_AGENTIC",
            new=False,
            create=True,
        ),
    ):
        from app.workers.assist_agent_worker import main

        await main()
        build_agent.assert_not_called()
