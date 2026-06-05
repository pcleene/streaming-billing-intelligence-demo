"""PR-AG tests for the agentic routing inside `AiAssistService`.

Verifies the flag-driven branch + the linear fallback paths. Existing
PR-8 tests in `test_ai_assist_service.py` still cover the linear path's
denormalisation + RAG-feedback behaviour; the tests here focus on the
*routing* contract introduced by PR-AG.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.feature_flags import flags
from app.services.ai_assist_service import AiAssistService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _case(*, ai_assist=None) -> dict:
    return {
        "case_id": "case-1",
        "customer_id": "cust_001",
        "status": "open",
        "severity": "medium",
        "rules_triggered": [
            {
                "rule_id": "r1",
                "rule_type": "discount_mismatch",
                "rule_name": "Discount mismatch",
                "severity": "medium",
            }
        ],
        "ai_assist": ai_assist,
    }


def _rag_payload() -> dict:
    return {
        "case_id": "case-1",
        "similar_cases": [
            {
                "case_id": "case-h-1",
                "score": 0.82,
                "disposition": "true_positive",
            }
        ],
        "assist": {
            "summary": "Linear path summary.",
            "likelihood": "true_positive",
            "confidence": 0.78,
            "rationale": ["linear-rationale"],
            "recommended_steps": ["linear-step"],
            "references": [],
        },
        "degraded": False,
    }


def _agent_state(*, trace_len: int = 6) -> dict:
    """Minimal `AssistAgentState` shape mirroring what the LangGraph
    runnable returns. `trace` is a list of dicts (the worker
    summary tests cover the field-by-field schema)."""
    trace = [
        {
            "node": name,
            "started_at": _utcnow().isoformat(),
            "finished_at": _utcnow().isoformat(),
            "duration_ms": 1.0,
            "status": "ok",
            "output_summary": {"keys": []},
        }
        for name in (
            "load_case",
            "classify",
            "analytics",
            "vector_search",
            "synthesize",
            "persist",
        )[:trace_len]
    ]
    return {
        "case_id": "case-1",
        "case": {"case_id": "case-1"},
        "classification": {"recommended_path": "deep"},
        "analytics": {},
        "retrieval": [
            {
                "case_id": "case-h-1",
                "score": 0.91,
                "disposition": "true_positive",
                "why_relevant": "agentic-similarity",
            }
        ],
        "synthesis": {
            "summary": "Agentic path summary.",
            "likelihood": "true_positive",
            "confidence": 0.82,
            "rationale": ["agentic-rationale"],
            "recommended_steps": ["agentic-step"],
            "references": [
                {
                    "case_id": "case-h-1",
                    "disposition": "true_positive",
                    "score": 0.91,
                    "why_relevant": "agentic-similarity",
                }
            ],
            "degraded": False,
            "degraded_reason": None,
        },
        "trace": trace,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_generate_routes_through_agent_when_flag_on_and_agent_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on + agent injected → agent.run is awaited and rag.assist
    is NOT called."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", True)

    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=_agent_state())

    svc = AiAssistService(case_repo, rag, agent=agent)
    out = await svc.generate("case-1")

    assert out["cached"] is False
    agent.run.assert_awaited_once_with(case_id="case-1")
    rag.assist.assert_not_called()
    # Synthesis from the agent state survives onto the persisted assist.
    assert out["ai_assist"]["summary"] == "Agentic path summary."
    assert out["ai_assist"]["likelihood"] == "true_positive"


@pytest.mark.asyncio
async def test_generate_uses_linear_path_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off → linear path runs even if an agent is wired in."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", False)

    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist = AsyncMock(return_value=_rag_payload())
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=_agent_state())

    svc = AiAssistService(case_repo, rag, agent=agent)
    out = await svc.generate("case-1")

    assert out["cached"] is False
    rag.assist.assert_awaited_once()
    agent.run.assert_not_called()
    assert out["ai_assist"]["summary"] == "Linear path summary."


@pytest.mark.asyncio
async def test_generate_uses_linear_path_when_no_agent_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on but no agent → still falls back to the linear RAG path."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", True)

    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist = AsyncMock(return_value=_rag_payload())

    svc = AiAssistService(case_repo, rag)  # no agent kwarg
    out = await svc.generate("case-1")

    assert out["cached"] is False
    rag.assist.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_persists_agent_trace_on_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agentic path must persist `agent_trace` on the case's
    ai_assist projection so the UI can render the reasoning path."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", True)

    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=_agent_state(trace_len=6))

    svc = AiAssistService(case_repo, rag, agent=agent)
    await svc.generate("case-1")

    persisted = case_repo.update_one.call_args.args[1]["$set"]["ai_assist"]
    assert "agent_trace" in persisted
    assert len(persisted["agent_trace"]) == 6
    assert persisted["agent_trace"][0]["node"] == "load_case"


@pytest.mark.asyncio
async def test_generate_freshness_cache_short_circuits_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh `ai_assist` projection must short-circuit BEFORE the
    agent or RAG runs, regardless of the flag state."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", True)

    fresh = {
        "summary": "cached",
        "likelihood": "true_positive",
        "confidence": 0.9,
        "rationale": ["x"],
        "recommended_steps": ["y"],
        "references": [],
        "generated_at": _utcnow().isoformat(),
    }
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case(ai_assist=fresh)
    rag = AsyncMock()
    agent = AsyncMock()

    svc = AiAssistService(case_repo, rag, agent=agent, freshness_seconds=300)
    out = await svc.generate("case-1")

    assert out["cached"] is True
    rag.assist.assert_not_called()
    agent.run.assert_not_called()
    case_repo.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_generate_agentic_handles_degraded_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the agent reports errors, the persisted assist must mark
    `degraded=True` and surface the joined error string."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", True)

    state = _agent_state()
    state["errors"] = ["analytics: timeout", "vector_search: 503"]
    state["synthesis"]["degraded"] = True
    state["synthesis"]["degraded_reason"] = "; ".join(state["errors"])

    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=state)

    svc = AiAssistService(case_repo, rag, agent=agent)
    out = await svc.generate("case-1")

    assert out["ai_assist"]["degraded"] is True
    assert "analytics: timeout" in out["ai_assist"]["degraded_reason"]


@pytest.mark.asyncio
async def test_existing_linear_test_still_passes_with_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity-check: stale cache + flag-off must still take the linear
    path exactly as the PR-8 test expects (regression guard for the
    extracted `_generate_linear` body)."""
    monkeypatch.setattr(flags, "AI_ASSIST_AGENTIC", False)

    stale = {
        "summary": "old",
        "likelihood": "true_positive",
        "confidence": 0.5,
        "rationale": ["x"],
        "recommended_steps": ["y"],
        "references": [],
        "generated_at": (_utcnow() - timedelta(hours=2)).isoformat(),
    }
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case(ai_assist=stale)
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist = AsyncMock(return_value=_rag_payload())

    svc = AiAssistService(case_repo, rag, freshness_seconds=300)
    out = await svc.generate("case-1")

    assert out["cached"] is False
    rag.assist.assert_awaited_once()
    persisted = case_repo.update_one.call_args.args[1]["$set"]["ai_assist"]
    assert persisted["likelihood"] == "true_positive"
    assert persisted["retrieval"]["k"] == 5
