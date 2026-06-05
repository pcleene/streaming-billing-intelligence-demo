"""Periodic SLA sweeper for open quarantine cases.

Pairs with `app.repositories.quarantine_case_repo.QuarantineCaseRepository.sweep_sla(...)`.
Runs in its own background process and, once per minute, recomputes the
`elapsed_minutes`, `minutes_to_breach`, `is_breached`, and `breached_at`
fields on every open case so dashboards and alerts can rely on
timezone-aware (UTC) values without polling the SLA math themselves.

The actual recomputation lives entirely on the repository; this module
is just the loop + logging skeleton (mirrors
`app.workers.charge_code_change_watcher`).

PR-8 extension: when the `AI_ASSIST_AUTO_RUN` feature flag is on, every
sweep tick also walks a bounded slice of open cases that lack an
`ai_assist` projection and fires `AiAssistService.generate(case_id)`
against each of them. Per-case failures are caught so one bad case can't
stall the whole batch.

Run via:  python -m app.workers.case_lifecycle_worker
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.services.ai_assist_service import AiAssistService

logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS: int = 60

# How many open-case-without-ai_assist candidates a single sweep tick will
# attempt to materialise. Keeps a sweep bounded so it can't stall the loop
# (and keeps Bedrock spend predictable) when a backlog of new cases lands.
# Lowered from 25 → 3 for the bounded-budget agentic retest; production
# can tune back up once spend signal is in place.
AI_ASSIST_MAX_PER_TICK: int = 3


class _Sweeper:
    def __init__(
        self,
        *,
        repo: QuarantineCaseRepository | None = None,
        ai_assist_service: AiAssistService | None = None,
        interval_seconds: int = SWEEP_INTERVAL_SECONDS,
        auto_run_ai_assist: bool | None = None,
        max_ai_assist_per_tick: int = AI_ASSIST_MAX_PER_TICK,
    ) -> None:
        self._repo = repo
        self._ai_assist = ai_assist_service
        self._interval = interval_seconds
        self._max_ai_assist_per_tick = max_ai_assist_per_tick
        self._stop = asyncio.Event()
        # Default to feature flag if not explicitly overridden. We import
        # lazily so tests can monkeypatch the flag without re-importing
        # the worker module.
        if auto_run_ai_assist is None:
            from app.core.feature_flags import flags
            self._auto_run = flags.AI_ASSIST_AUTO_RUN
        else:
            self._auto_run = auto_run_ai_assist

    async def run_once(self) -> dict:
        """Drive a single SLA recomputation pass and (optionally) fan out
        ai-assist generation. Returns merged counters for logging.
        """
        sla_counters = await self._repo.sweep_sla(now=datetime.now(timezone.utc))
        ai_counters = {"considered": 0, "generated": 0, "skipped": 0, "errors": 0}
        if self._auto_run and self._ai_assist is not None:
            ai_counters = await self._auto_run_ai_assist()
        logger.info(
            "case_lifecycle_sweep",
            scanned=sla_counters.get("scanned"),
            updated=sla_counters.get("updated"),
            newly_breached=sla_counters.get("newly_breached"),
            ai_considered=ai_counters.get("considered"),
            ai_generated=ai_counters.get("generated"),
            ai_skipped=ai_counters.get("skipped"),
            ai_errors=ai_counters.get("errors"),
        )
        return {**sla_counters, "ai_assist": ai_counters}

    async def _auto_run_ai_assist(self) -> dict:
        """Walk a bounded slice of open cases without ai_assist and fire
        `AiAssistService.generate(case_id)` against each. Per-case
        failures are absorbed so one error can't kill the batch.
        """
        counters = {"considered": 0, "generated": 0, "skipped": 0, "errors": 0}
        # Pull a window large enough to fill the per-tick budget even if
        # most cases already have an ai_assist projection.
        candidates = await self._repo.list_open(
            limit=max(self._max_ai_assist_per_tick * 4, self._max_ai_assist_per_tick)
        )
        for case in candidates:
            counters["considered"] += 1
            if case.get("ai_assist"):
                counters["skipped"] += 1
                continue
            if counters["generated"] + counters["errors"] >= self._max_ai_assist_per_tick:
                # Hit the per-tick budget. Remaining cases will be picked
                # up next minute.
                break
            case_id = case.get("case_id")
            if not case_id:
                counters["skipped"] += 1
                continue
            try:
                await self._ai_assist.generate(case_id=case_id)
                counters["generated"] += 1
            except Exception as exc:  # noqa: BLE001 — bounded by per-case scope
                counters["errors"] += 1
                logger.warning(
                    "case_lifecycle_ai_assist_failed",
                    case_id=case_id,
                    error=str(exc),
                )
        return counters

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
            except asyncio.TimeoutError:
                # Interval elapsed without a shutdown request — loop on.
                continue

    async def shutdown(self) -> None:
        self._stop.set()


def _build_ai_assist_service(db) -> AiAssistService:
    """Compose an `AiAssistService` against a live DB handle.

    Mirrors the FastAPI dependency in `app.routes.quarantine` so the
    worker and the explicit POST route share the same wiring (case repo
    + history repo + bedrock). AutoEmbed (ADR-032) removed the
    application-side Voyage client — RagService talks to Atlas via
    `$vectorSearch.query` directly.

    When `FF_AI_ASSIST_AGENTIC` is on we additionally construct the
    `AssistAgent` and hand it to the service so `generate()` routes
    through `_generate_agentic` (LangGraph) instead of the linear RAG
    path. The agent build is deferred behind the flag so the
    LangGraph import + repo wiring stay cheap when the flag is off.
    """
    # Imports are local so we only pay them when the flag is on.
    from app.core.feature_flags import flags
    from app.llm.bedrock_client import BedrockClient
    from app.repositories.case_history_repo import CaseHistoryRepository
    from app.services.rag_service import RagService

    case_repo = QuarantineCaseRepository(db)
    history_repo = CaseHistoryRepository(db)
    rag = RagService(
        case_repo,
        history_repo,
        BedrockClient(),
    )

    agent = None
    if flags.AI_ASSIST_AGENTIC:
        from app.workers.assist_agent_worker import _build_agent
        agent = _build_agent(db)

    return AiAssistService(
        case_repo,
        rag,
        history_repo=history_repo,
        agent=agent,
    )


async def main() -> None:
    configure_logging()
    await connect_mongo()
    db = get_db()
    repo = QuarantineCaseRepository(db)

    # Lazy: only build the (heavyweight) AI assist stack when the flag
    # is on. Keeps the worker cheap for the default-off rollout.
    from app.core.feature_flags import flags
    ai_assist: AiAssistService | None = None
    if flags.AI_ASSIST_AUTO_RUN:
        ai_assist = _build_ai_assist_service(db)

    sweeper = _Sweeper(repo=repo, ai_assist_service=ai_assist)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(sweeper.shutdown())
        )
    try:
        await sweeper.run()
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
