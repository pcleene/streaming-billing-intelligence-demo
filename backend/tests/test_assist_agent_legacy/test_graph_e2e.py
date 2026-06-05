"""End-to-end LangGraph orchestration tests.

These tests build the compiled graph with `AsyncMock`-backed deps and
drive it via `AssistAgent.run(case_id=...)`, asserting:

  * the topology — fast vs deep routing — by checking which nodes
    appear in the final `state['trace']`,
  * graceful degradation — a vector_search failure still produces a
    synthesis (with `degraded=True`) rather than aborting,
  * persistence hand-off — the graph calls `deps.persist_fn(...)`
    exactly once with the final synthesis + trace.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.assist_agent import AssistAgent
from app.services.assist_agent.defaults import (
    default_classifier,
    default_synthesizer,
)
from app.services.assist_agent.nodes import AssistAgentDeps


def _rule(rule_type: str = "discount_mismatch", confidence: float | None = None) -> dict:
    ev: dict = {}
    if confidence is not None:
        ev["confidence"] = confidence
    return {
        "rule_id": "r1",
        "rule_type": rule_type,
        "rule_name": "test rule",
        "severity": "medium",
        "evidence": ev,
    }


def _case_high() -> dict:
    return {
        "case_id": "case-1",
        "customer_id": "cust-1",
        "severity": "high",
        "rules_triggered": [
            _rule(rule_type="discount_mismatch", confidence=0.91),
            _rule(rule_type="overcharge", confidence=0.65),
        ],
    }


def _case_low() -> dict:
    return {
        "case_id": "case-2",
        "customer_id": "cust-2",
        "severity": "low",
        "rules_triggered": [],
    }


def _make_deps(
    *,
    case: dict,
    rag_hits: list[dict] | None = None,
    vector_search_fn=None,
    persist_fn=None,
) -> AssistAgentDeps:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = case

    analytics_tools = MagicMock()
    analytics_tools.customer_pattern_30d = AsyncMock(return_value={
        "avg_amount": 100.0, "stddev_amount": 12.0,
        "top_charge_codes": ["A"], "txn_count": 30, "settled_count": 28,
    })
    analytics_tools.rule_type_frequency = AsyncMock(return_value={
        "count": 5, "distinct_customers": 4, "top_evidences": [],
    })
    analytics_tools.drift_snapshot = AsyncMock(return_value={"snapshots": []})

    if vector_search_fn is None:
        vector_search_fn = AsyncMock(
            return_value=rag_hits if rag_hits is not None else [
                {"case_id": "case-h-1", "score": 0.82,
                 "disposition": "true_positive"},
            ]
        )
    if persist_fn is None:
        persist_fn = AsyncMock(return_value=None)

    classify_fn = AsyncMock(side_effect=lambda c: default_classifier(c))
    synthesize_fn = AsyncMock(side_effect=lambda s: default_synthesizer(s))

    return AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=classify_fn,
        analytics_tools=analytics_tools,
        vector_search_fn=vector_search_fn,
        synthesize_fn=synthesize_fn,
        persist_fn=persist_fn,
    )


def _node_names(state: dict) -> list[str]:
    return [t.get("node") for t in (state.get("trace") or [])]


async def test_deep_path_runs_all_six_nodes() -> None:
    """High-severity, multi-rule case → classifier picks 'deep' → graph
    runs every node in order."""
    case = _case_high()
    deps = _make_deps(case=case)
    agent = AssistAgent(deps)
    final = await agent.run(case_id="case-1")

    nodes = _node_names(final)
    assert nodes == [
        "load_case", "classify", "analytics",
        "vector_search", "synthesize", "persist",
    ]
    # All six should be error-free.
    for entry in final["trace"]:
        assert entry.get("error") is None, entry
    # Synthesis was produced.
    assert final.get("synthesis"), "expected synthesis dict in final state"
    assert final["synthesis"]["summary"]
    # Persist hand-off happened exactly once.
    deps.persist_fn.assert_awaited_once()


async def test_fast_path_skips_analytics() -> None:
    """Low-severity, no-rule case → classifier picks 'fast' → analytics
    node is NOT visited (5 entries instead of 6)."""
    case = _case_low()
    deps = _make_deps(case=case)
    agent = AssistAgent(deps)
    final = await agent.run(case_id="case-2")

    nodes = _node_names(final)
    assert "analytics" not in nodes, f"expected fast path, got {nodes}"
    assert nodes == [
        "load_case", "classify",
        "vector_search", "synthesize", "persist",
    ]
    # Analytics tools should NOT have been called.
    deps.analytics_tools.customer_pattern_30d.assert_not_awaited()
    deps.analytics_tools.rule_type_frequency.assert_not_awaited()
    deps.analytics_tools.drift_snapshot.assert_not_awaited()
    # And synthesis still ran.
    assert final.get("synthesis")
    deps.persist_fn.assert_awaited_once()


async def test_vector_search_failure_degrades_gracefully() -> None:
    """vector_search raises → trace records error → synthesize still
    runs and returns a degraded payload (rationale + degraded flag)."""
    case = _case_high()
    failing_search = AsyncMock(side_effect=RuntimeError("opensearch unreachable"))
    deps = _make_deps(case=case, vector_search_fn=failing_search)
    agent = AssistAgent(deps)
    final = await agent.run(case_id="case-1")

    nodes = _node_names(final)
    # Synthesize must still run despite the upstream error.
    assert "synthesize" in nodes
    assert "persist" in nodes
    # Error trace entry for vector_search.
    vs_entry = next(t for t in final["trace"] if t["node"] == "vector_search")
    assert vs_entry["error"] and "opensearch unreachable" in vs_entry["error"]
    # Synthesis is present and degraded.
    syn = final["synthesis"]
    assert syn is not None
    assert syn["degraded"] is True
    assert syn["degraded_reason"]
    assert syn["rationale"]
    deps.persist_fn.assert_awaited_once()
