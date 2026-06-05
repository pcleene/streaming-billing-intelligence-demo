"""End-to-end tests for the compiled LangGraph assist-agent.

These tests run the actual compiled graph (no DB / no LLM): every dep
is an `AsyncMock` or plain async lambda. Goal: prove the routing
edges + reducer wiring behave as advertised, including the fast-vs-deep
conditional and the load_case-failure short-circuit.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.assist_agent import AssistAgent, AssistAgentDeps


def _make_deps(
    *,
    classification: dict | None = None,
    case: dict | None = None,
    retrieval: list[dict] | None = None,
    synthesis: dict | None = None,
    customer_pattern: dict | None = None,
    rule_type_frequency: dict | None = None,
    drift_snapshot: dict | None = None,
    case_repo_returns: Any = "default",
) -> AssistAgentDeps:
    """Build deps for an end-to-end run with sensible defaults.

    `case_repo_returns="default"` means "use the default fake case";
    pass `None` to simulate a missing case.
    """
    default_case = case or {
        "case_id": "QC-1",
        "customer_id": "CUST-1",
        "rules_triggered": [
            {"rule_type": "DUP", "evidence": {"feature_name": "f1"}},
        ],
    }
    case_repo = AsyncMock()
    if case_repo_returns == "default":
        case_repo.get_by_id = AsyncMock(return_value=default_case)
    else:
        case_repo.get_by_id = AsyncMock(return_value=case_repo_returns)

    class _Tools:
        def __init__(self) -> None:
            self.customer_pattern_30d = AsyncMock(
                return_value=customer_pattern or {"txn_count": 1}
            )
            self.rule_type_frequency = AsyncMock(
                return_value=rule_type_frequency or {"count": 1}
            )
            self.drift_snapshot = AsyncMock(
                return_value=drift_snapshot or {"snapshots": []}
            )

    tools = _Tools()

    classify_fn = AsyncMock(
        return_value=classification
        if classification is not None
        else {"recommended_path": "deep", "complexity": "high"}
    )
    vector_search_fn = AsyncMock(
        return_value=retrieval or [{"case_id": "HIST-1", "score": 0.9}]
    )
    synthesize_fn = AsyncMock(
        return_value=synthesis
        or {"summary": "ok", "likelihood": "true_positive", "confidence": 0.7}
    )
    persist_fn = AsyncMock(return_value=None)

    return AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=classify_fn,
        analytics_tools=tools,
        vector_search_fn=vector_search_fn,
        synthesize_fn=synthesize_fn,
        persist_fn=persist_fn,
    )


def _node_order(state: dict) -> list[str]:
    return [t["node"] for t in (state.get("trace") or [])]


# --- happy paths ------------------------------------------------------


async def test_end_to_end_deep_path_runs_all_seven_nodes_in_order():
    deps = _make_deps(classification={"recommended_path": "deep"})
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    assert _node_order(final) == [
        "load_case",
        "load_live_state",
        "classify",
        "analytics",
        "vector_search",
        "synthesize",
        "persist",
    ]
    deps.analytics_tools.customer_pattern_30d.assert_awaited_once()
    deps.synthesize_fn.assert_awaited_once()
    deps.persist_fn.assert_awaited_once()


async def test_end_to_end_fast_path_skips_analytics():
    deps = _make_deps(classification={"recommended_path": "fast"})
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    nodes = _node_order(final)
    assert "analytics" not in nodes
    assert nodes == [
        "load_case",
        "load_live_state",
        "classify",
        "vector_search",
        "synthesize",
        "persist",
    ]
    # The three analytics tools must NOT have been awaited on the fast path.
    deps.analytics_tools.customer_pattern_30d.assert_not_awaited()
    deps.analytics_tools.rule_type_frequency.assert_not_awaited()
    deps.analytics_tools.drift_snapshot.assert_not_awaited()


# --- failure / fallback ----------------------------------------------


async def test_missing_case_terminates_before_synthesize_and_persist():
    deps = _make_deps(case_repo_returns=None)
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-MISSING")
    nodes = _node_order(final)
    # Only load_case ran; the conditional edge sent us to END.
    assert nodes == ["load_case"]
    deps.classify_fn.assert_not_awaited()
    deps.synthesize_fn.assert_not_awaited()
    deps.persist_fn.assert_not_awaited()
    assert any("case_not_found" in e for e in final.get("errors") or [])


async def test_default_path_when_classification_missing_is_deep():
    """Empty classifier output should NOT silently degrade — the
    router defaults to "deep" so analytics still runs."""
    deps = _make_deps(classification={})
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    nodes = _node_order(final)
    assert "analytics" in nodes
    assert nodes == [
        "load_case",
        "load_live_state",
        "classify",
        "analytics",
        "vector_search",
        "synthesize",
        "persist",
    ]


async def test_load_live_state_hydrates_customer_and_transaction_when_deps_wired():
    """When `customer_360_service` + `transaction_repo` are passed in,
    the `load_live_state` node populates the matching state keys
    before classification runs."""
    from unittest.mock import AsyncMock as _AM

    customer_doc = {"customer_id": "CUST-1", "tier": "Gold"}
    transaction_doc = {
        "transaction_id": "TXN-1",
        "items": [{"charge_code": "CC_HD"}],
    }
    cust_svc = _AM()
    cust_svc.get_profile = _AM(return_value=customer_doc)
    txn_repo = _AM()
    txn_repo.get_by_id = _AM(return_value=transaction_doc)

    case = {
        "case_id": "QC-1",
        "customer_id": "CUST-1",
        "transaction_summary": {"transaction_id": "TXN-1"},
        "rules_triggered": [
            {"rule_type": "DUP", "evidence": {"feature_name": "f1"}},
        ],
    }
    deps = _make_deps(case=case, classification={"recommended_path": "fast"})
    deps.customer_360_service = cust_svc
    deps.transaction_repo = txn_repo

    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    assert final.get("customer") == customer_doc
    assert final.get("transaction") == transaction_doc
    cust_svc.get_profile.assert_awaited_once()
    txn_repo.get_by_id.assert_awaited_once()
    # The synthesize_fn sees the hydrated customer + transaction.
    synth_call = deps.synthesize_fn.await_args
    state_arg = synth_call.args[0] if synth_call.args else None
    if state_arg is not None:
        assert state_arg.get("customer") == customer_doc
        assert state_arg.get("transaction") == transaction_doc


async def test_trace_entries_are_chronologically_ordered():
    deps = _make_deps(classification={"recommended_path": "deep"})
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    trace = final["trace"]
    starts = [t["started_at"] for t in trace]
    # `started_at` is serialised as ISO-8601 strings by `model_dump(mode="json")`
    # — sorting lexicographically on that format is equivalent to chronological.
    assert starts == sorted(starts), f"trace not in time order: {starts}"


async def test_all_deps_are_async_mock_able_no_real_io():
    """Build the agent with bare `AsyncMock`s — no Mongo, no LLM, no
    repo wiring. If anything in the graph quietly reaches outside the
    `deps` boundary this test would either hang or attempt I/O."""

    case_repo = AsyncMock()
    case_repo.get_by_id = AsyncMock(
        return_value={
            "case_id": "QC-1",
            "customer_id": "C1",
            "rules_triggered": [{"rule_type": "X", "evidence": {}}],
        }
    )

    class _Tools:
        customer_pattern_30d = AsyncMock(return_value={})
        rule_type_frequency = AsyncMock(return_value={})
        drift_snapshot = AsyncMock(return_value={})

    deps = AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=AsyncMock(return_value={"recommended_path": "deep"}),
        analytics_tools=_Tools(),
        vector_search_fn=AsyncMock(return_value=[]),
        synthesize_fn=AsyncMock(return_value={"summary": "ok"}),
        persist_fn=AsyncMock(return_value=None),
    )
    agent = AssistAgent(deps)
    final = await agent.run(case_id="QC-1")
    assert "synthesize" in _node_order(final)
    assert "persist" in _node_order(final)
    deps.persist_fn.assert_awaited_once()
