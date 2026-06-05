"""Agent observability surface (PR-12 / G2).

Surfaces the LangGraph AI-assist agent's per-node `agent_trace` plus a
compact run summary, and offers a bounded-concurrency batch trigger so
analysts can spend Bedrock budget on a queue of cases at once.

The `agent_trace` is written by `AiAssistService._generate_agentic`
onto `quarantine_cases.ai_assist.agent_trace` (see
`app/services/ai_assist_service.py`). Trace entries follow the
`AgentTraceEntry` shape from `app/services/assist_agent/state.py`:

    {
        "node":        str,
        "started_at":  datetime,
        "finished_at": datetime,
        "duration_ms": float,
        "output_keys": list[str],
        "error":       str | None,
        "metadata":    dict,
    }

`AgentObservabilityService` only *reads* this projection — it never
mutates the persisted trace. Mutation stays the responsibility of the
agent-runner path inside `AiAssistService`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import CaseNotFound
from app.core.logging import get_logger
from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from app.services.ai_assist_service import AiAssistService

logger = get_logger(__name__)


# Default concurrency cap for batch_generate. Tuned conservatively so a
# stampede of cases doesn't burn the Bedrock per-minute quota.
DEFAULT_BATCH_CONCURRENCY = 4


class AgentObservabilityService:
    """Read-side surface over the agent's persisted trace + batch driver.

    Wires together `QuarantineCaseRepository` (to fetch the trace blob)
    and `AiAssistService` (to drive `generate(...)` for batch).
    """

    def __init__(
        self,
        case_repo: QuarantineCaseRepository,
        ai_assist: AiAssistService,
    ) -> None:
        self._cases = case_repo
        self._ai_assist = ai_assist

    async def get_trace(self, case_id: str) -> dict | None:
        """Return the trace projection for a case.

        Returns:
            ``None`` if the case doesn't exist.
            Otherwise: ``{"case_id", "has_trace", "trace", "summary"}``
            where ``has_trace`` is False (and ``trace=[]``) when the
            case exists but has not been driven through the agent yet.
        """
        case = await self._cases.get_by_id(case_id)
        if case is None:
            return None
        ai_assist = case.get("ai_assist") or {}
        trace = ai_assist.get("agent_trace") or []
        # Be defensive against legacy / malformed projections — anything
        # that isn't a list collapses to "no trace yet" rather than
        # blowing up the analyst's request.
        if not isinstance(trace, list):
            trace = []
        has_trace = bool(trace)
        summary = await self.summarise_trace(trace)
        return {
            "case_id": case_id,
            "has_trace": has_trace,
            "trace": list(trace),
            "summary": summary,
        }

    async def summarise_trace(self, trace: list[dict]) -> dict:
        """Project a raw trace list into the analyst-facing summary.

        Output keys:
            * ``path_taken``     — ordered, deduplicated node names
              (preserves first-seen order so re-entrant nodes don't
              show up twice in the timeline ribbon).
            * ``nodes_run``      — count of distinct node names.
            * ``error_count``    — entries whose ``error`` field is
              truthy *or* whose ``status`` is ``"error"``.
            * ``total_duration_ms`` — sum of ``duration_ms`` across
              entries that supply it; ``None`` when no entry carries
              that field (so the UI can show ``--`` instead of 0ms).
        """
        path_taken: list[str] = []
        seen_nodes: set[str] = set()
        error_count = 0
        duration_total: float = 0.0
        any_duration = False

        for entry in trace or []:
            if not isinstance(entry, dict):
                # Skip stray non-dict items rather than break the
                # whole summary — keeps the UI resilient.
                continue
            node = entry.get("node")
            if isinstance(node, str) and node and node not in seen_nodes:
                seen_nodes.add(node)
                path_taken.append(node)
            if entry.get("error") or entry.get("status") == "error":
                error_count += 1
            dur = entry.get("duration_ms")
            if isinstance(dur, (int, float)):
                duration_total += float(dur)
                any_duration = True

        return {
            "path_taken": path_taken,
            "nodes_run": len(seen_nodes),
            "error_count": error_count,
            "total_duration_ms": duration_total if any_duration else None,
        }

    async def batch_generate(
        self,
        case_ids: list[str],
        *,
        force: bool = False,
        max_concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    ) -> dict:
        """Drive `AiAssistService.generate` over many case_ids in parallel.

        Returns counters the route layer surfaces verbatim:
            ``{
                "requested": int,
                "generated": int,   # cached=False results
                "skipped":   int,   # cached=True results
                "errors":    [{case_id, reason}, ...]
            }``

        Concurrency is bounded by an `asyncio.Semaphore` (default 4) so
        we don't fan out the entire batch at Bedrock simultaneously.

        The 25-id cap is enforced at the *route* layer (validator) so
        this method stays composable for non-HTTP callers (e.g. the
        worker fanning out a backlog).
        """
        if max_concurrency < 1:
            max_concurrency = 1
        sem = asyncio.Semaphore(max_concurrency)

        generated = 0
        skipped = 0
        errors: list[dict[str, str]] = []

        async def _run_one(cid: str) -> None:
            nonlocal generated, skipped
            async with sem:
                try:
                    out = await self._ai_assist.generate(cid, force=force)
                except CaseNotFound:
                    errors.append({"case_id": cid, "reason": "case_not_found"})
                    return
                except Exception as exc:  # noqa: BLE001
                    # Non-fatal: capture the message; one bad case
                    # mustn't poison the rest of the batch.
                    logger.warning(
                        "agent_obs_batch_case_failed",
                        case_id=cid,
                        error=str(exc),
                    )
                    errors.append({"case_id": cid, "reason": str(exc)})
                    return
                if out.get("cached"):
                    skipped += 1
                else:
                    generated += 1

        # `gather` with return_exceptions=False is safe because
        # `_run_one` already swallows per-case errors into `errors`.
        await asyncio.gather(*(_run_one(cid) for cid in case_ids))

        return {
            "requested": len(case_ids),
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
        }


__all__ = ["AgentObservabilityService", "DEFAULT_BATCH_CONCURRENCY"]
