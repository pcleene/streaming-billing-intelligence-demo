"""Change-stream-driven worker for the LangGraph AI-assist agent (PR-AG).

Tails the `quarantine_cases` collection for `insert` events and, for
every newly-opened case, dispatches an `AssistAgent.run(case_id=...)`
through a bounded `asyncio.Semaphore`. The worker is the *only* place
where `state["trace"]` is rolled up into a structured
`AssistAgentRunSummary` log entry — the graph itself has no DB writes
so it stays re-runnable.

Topology mirrors `app.workers.feature_engineer`: `coll.watch()` →
`asyncio.wait_for(stream.next(), timeout=...)` → per-event
`asyncio.create_task` so a slow node doesn't stall the stream tail.

Feature-flag contract:
  * `flags.AI_ASSIST_AGENTIC=False` → `main()` short-circuits before
    constructing the (heavyweight) agent stack.
  * Direct `_AgentWorker(enabled=False)` instantiation logs
    `"agentic_disabled_skip"` and exits its run loop after one tick so
    integration tests can assert the no-op path without flipping env vars.

Run via:  python -m app.workers.assist_agent_worker
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from pydantic import BaseModel, Field

from app.core.constants import QUARANTINE_CASES
from app.core.feature_flags import flags
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db

logger = get_logger(__name__)


# Default per-worker concurrency cap. Each in-flight agent run holds
# one Bedrock call + one vector search + three analytics queries, so a
# small fan-out keeps the per-pod resource footprint predictable.
DEFAULT_MAX_CONCURRENT: int = 4


class AssistAgentRunSummary(BaseModel):
    """Structured per-case log line emitted after each agent run.

    Folds `state['trace']` into a flat shape so log aggregators can
    pivot on `path_taken` / `nodes_run` / `error_count` without parsing
    nested JSON.
    """

    case_id: str
    path_taken: str = Field(
        description=(
            "'deep' when the analytics node ran, 'fast' otherwise. "
            "Inferred from whether any trace entry has node='analytics'."
        )
    )
    nodes_run: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    degraded: bool = False


def _summarise_state(case_id: str, state: dict[str, Any]) -> AssistAgentRunSummary:
    """Roll up a final agent state into a flat run summary.

    Pure function so it can be unit-tested without spinning the graph.
    """
    trace = list(state.get("trace") or [])
    errors = list(state.get("errors") or [])
    synthesis = state.get("synthesis") or {}
    # `path_taken` is inferred from the trace: the analytics node only
    # runs on the deep branch (see graph.py conditional edge).
    path_taken = "deep" if any(
        isinstance(e, dict) and e.get("node") == "analytics" for e in trace
    ) else "fast"
    total_duration_ms = 0.0
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        d = entry.get("duration_ms")
        try:
            if d is not None:
                total_duration_ms += float(d)
        except (TypeError, ValueError):
            continue
    return AssistAgentRunSummary(
        case_id=case_id,
        path_taken=path_taken,
        nodes_run=len(trace),
        error_count=len(errors),
        total_duration_ms=total_duration_ms,
        degraded=bool(synthesis.get("degraded") or errors),
    )


class _AgentWorker:
    """Change-stream consumer dispatching new cases to the agent."""

    def __init__(
        self,
        *,
        db: Any,
        agent: Any | None = None,
        enabled: bool | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._db = db
        self._agent = agent
        # Defaulting `enabled` to the flag (rather than reading inside
        # `run()`) lets tests pass `enabled=False` directly without
        # monkeypatching module state.
        self._enabled = (
            flags.AI_ASSIST_AGENTIC if enabled is None else bool(enabled)
        )
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        """Tail the change stream and dispatch agent runs.

        When the worker is disabled we still log a single line so ops
        can confirm the pod is alive but intentionally idle, then exit
        the loop so Kubernetes doesn't restart-loop a no-op.
        """
        if not self._enabled:
            logger.info("agentic_disabled_skip")
            return
        if self._agent is None:
            logger.warning(
                "assist_agent_worker_no_agent_wired",
                detail="enabled=True but agent=None; exiting",
            )
            return

        coll = self._db[QUARANTINE_CASES]
        pipeline = [{"$match": {"operationType": "insert"}}]
        async with coll.watch(
            pipeline=pipeline, full_document="updateLookup"
        ) as stream:
            logger.info("assist_agent_worker_change_stream_open")
            while not self._stop.is_set():
                try:
                    change = await asyncio.wait_for(stream.next(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                doc = change.get("fullDocument") or {}
                case_id = doc.get("case_id")
                if not case_id:
                    continue
                task = asyncio.create_task(self._handle_case(case_id))
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
        # Wait for in-flight handlers to drain so the trace summary
        # logs land before the worker exits.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)

    async def _handle_case(self, case_id: str) -> None:
        """Run the agent for one case under the concurrency cap.

        We bound parallelism *here* (around the actual `agent.run` await)
        rather than in `run()` because the change-stream tail must keep
        consuming events even when `max_concurrent` runs are in-flight —
        otherwise a slow batch of cases would back-pressure the stream
        and the `wait_for` timeout would fire spuriously.
        """
        async with self._sem:
            try:
                state = await self._agent.run(case_id=case_id)
            except Exception as exc:  # noqa: BLE001
                # Bounded by per-case scope: one bad case can't kill the
                # tail loop. The change stream stays open and the next
                # event still gets handled.
                logger.warning(
                    "assist_agent_run_failed",
                    case_id=case_id,
                    error=str(exc),
                )
                return
            try:
                summary = _summarise_state(case_id, dict(state or {}))
                logger.info(
                    "assist_agent_run",
                    **summary.model_dump(),
                )
            except Exception as exc:  # noqa: BLE001
                # Summarisation must never mask a successful run.
                logger.warning(
                    "assist_agent_summary_failed",
                    case_id=case_id,
                    error=str(exc),
                )

    def _summarise(self, state: dict[str, Any]) -> AssistAgentRunSummary:
        """Instance accessor over the module-level pure summariser.

        Kept as an instance method so tests that already build a worker
        can call `worker._summarise(state)` directly without importing
        the helper.
        """
        case_id = (state or {}).get("case_id") or ""
        return _summarise_state(case_id, dict(state or {}))

    async def shutdown(self) -> None:
        self._stop.set()


def _build_agent(db: Any) -> Any:
    """Compose an `AssistAgent` against a live DB handle.

    Mirrors the FastAPI dep wiring in `app.routes.quarantine` so the
    worker and the explicit POST route share repos / embedding /
    Bedrock setup. Imports are local so we only pay them when the
    flag is on.

    Aligns kwargs with `AssistAgentDeps`'s current field names
    (`classify_fn`, `analytics_tools`, `vector_search_fn`,
    `synthesize_fn`, `persist_fn`). `persist_fn` is a no-op because
    `AiAssistService._generate_agentic` owns the canonical write
    when the agent is driven via the service; standalone worker mode
    still benefits from upstream node tracing without double-writing.
    """
    from app.llm.bedrock_client import BedrockClient
    from app.repositories.case_history_repo import CaseHistoryRepository
    from app.repositories.customer_repo import CustomerRepository
    from app.repositories.quarantine_case_repo import QuarantineCaseRepository
    from app.repositories.transaction_repo import TransactionRepository
    from app.services.assist_agent import (
        AssistAgent,
        AssistAgentDeps,
        default_classifier,
        default_synthesizer,
    )
    from app.services.assist_agent.tools import build_analytics_tools
    from app.services.customer_360_service import Customer360Service
    from app.services.rag_service import RagService

    case_repo = QuarantineCaseRepository(db)
    history_repo = CaseHistoryRepository(db)
    txn_repo = TransactionRepository(db)
    customer_repo = CustomerRepository(db)
    customer_360 = Customer360Service(customer_repo)
    # AutoEmbed (ADR-032): RagService no longer needs an embedding client;
    # `$vectorSearch.query` lets Atlas embed the query string in-cluster.
    rag = RagService(case_repo, history_repo, BedrockClient())
    analytics_tools = build_analytics_tools(db)

    async def _classify(case: dict) -> dict:
        # Sync helper wrapped as a coroutine to match the
        # `Callable[..., Awaitable[dict]]` contract on the deps.
        return default_classifier(case)

    async def _synthesize(state: dict) -> dict:
        return default_synthesizer(state)

    async def _vector_search(case: dict) -> list[dict]:
        case_id = case.get("case_id")
        if not case_id:
            return []
        result = await rag.assist(case_id, k=5, score_threshold=0.30)
        return list(result.get("similar_cases") or [])

    async def _persist_noop(case_id: str, synthesis: dict, trace: list) -> None:
        # AiAssistService._generate_agentic does the canonical write
        # (materialise + denorm + history feedback). Keep the agent's
        # persist node a no-op so the two paths can't double-write.
        return None

    deps = AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=_classify,
        analytics_tools=analytics_tools,
        vector_search_fn=_vector_search,
        synthesize_fn=_synthesize,
        persist_fn=_persist_noop,
        # Live-state hydration (used by the `load_live_state` node):
        # rich V3 customer doc + V3 transaction doc.
        customer_360_service=customer_360,
        transaction_repo=txn_repo,
    )
    return AssistAgent(deps=deps)


async def main() -> None:
    configure_logging()
    await connect_mongo()
    if not flags.AI_ASSIST_AGENTIC:
        logger.info("assist_agent_worker_disabled_via_flag")
        await disconnect_mongo()
        return
    db = get_db()
    agent = _build_agent(db)
    worker = _AgentWorker(db=db, agent=agent)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(worker.shutdown())
        )
    try:
        await worker.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
