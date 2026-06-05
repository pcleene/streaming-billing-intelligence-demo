"""Per-node unit tests for the AI-assist agent.

Each node is `async def fn(state, *, deps) -> dict` returning a *partial
state delta*. We invoke nodes directly with `AsyncMock`-backed deps —
no LangGraph in the loop here; the e2e test (`test_graph_e2e.py`)
verifies the topology.

Trace contract:
  * On success a single trace entry is appended with `error=None` and
    `output_keys` populated.
  * On failure the entry has `error="<ExceptionType>: <msg>"` and
    `state['errors']` accumulates the same.
  * Both fields use `operator.add` reducers in the LangGraph state, so
    each node returns a one-element list — the runtime concatenates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.assist_agent.defaults import default_classifier, default_synthesizer
from app.services.assist_agent.nodes import (
    AssistAgentDeps,
    analytics,
    classify,
    load_case,
    persist,
    synthesize,
    vector_search,
)


def _rule(
    rule_type: str = "discount_mismatch",
    feature_name: str | None = None,
    confidence: float | None = None,
) -> dict:
    ev: dict = {}
    if feature_name is not None:
        ev["feature_name"] = feature_name
    if confidence is not None:
        ev["confidence"] = confidence
    return {
        "rule_id": "r1",
        "rule_type": rule_type,
        "rule_name": "test rule",
        "severity": "medium",
        "evidence": ev,
    }


def _case(
    *,
    severity: str = "medium",
    rules: list[dict] | None = None,
    customer_id: str = "cust-1",
) -> dict:
    return {
        "case_id": "case-1",
        "customer_id": customer_id,
        "severity": severity,
        "rules_triggered": rules if rules is not None else [_rule()],
    }


def _make_deps(
    *,
    case: dict | None = None,
    pattern: dict | None = None,
    rule_freq: dict | None = None,
    drift: dict | None = None,
    rag_hits: list[dict] | None = None,
    classify_fn=None,
    synthesize_fn=None,
    persist_fn=None,
    vector_search_fn=None,
    case_repo_raises: Exception | None = None,
) -> AssistAgentDeps:
    case_repo = AsyncMock()
    if case_repo_raises is not None:
        case_repo.get_by_id.side_effect = case_repo_raises
    else:
        case_repo.get_by_id.return_value = case

    analytics_tools = MagicMock()
    analytics_tools.customer_pattern_30d = AsyncMock(
        return_value=pattern or {
            "avg_amount": 100.0,
            "stddev_amount": 12.0,
            "top_charge_codes": ["A"],
            "txn_count": 30,
            "settled_count": 28,
        }
    )
    analytics_tools.rule_type_frequency = AsyncMock(
        return_value=rule_freq or {
            "count": 5, "distinct_customers": 4, "top_evidences": [],
        }
    )
    analytics_tools.drift_snapshot = AsyncMock(
        return_value=drift or {"snapshots": []}
    )

    if vector_search_fn is None:
        vector_search_fn = AsyncMock(
            return_value=rag_hits if rag_hits is not None else [
                {"case_id": "case-h-1", "score": 0.82,
                 "disposition": "true_positive"},
            ]
        )

    if classify_fn is None:
        classify_fn = AsyncMock(side_effect=lambda c: default_classifier(c))
    if synthesize_fn is None:
        synthesize_fn = AsyncMock(side_effect=lambda s: default_synthesizer(s))
    if persist_fn is None:
        persist_fn = AsyncMock(return_value=None)

    return AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=classify_fn,
        analytics_tools=analytics_tools,
        vector_search_fn=vector_search_fn,
        synthesize_fn=synthesize_fn,
        persist_fn=persist_fn,
    )


# --- load_case --------------------------------------------------------


async def test_load_case_populates_state_case() -> None:
    case = _case()
    deps = _make_deps(case=case)
    state = {"case_id": "case-1", "trace": [], "errors": []}
    delta = await load_case(state, deps=deps)
    assert delta["case"] == case
    assert len(delta["trace"]) == 1
    entry = delta["trace"][0]
    assert entry["node"] == "load_case"
    assert entry.get("error") is None
    assert "case" in entry["output_keys"]


async def test_load_case_records_error_when_missing() -> None:
    deps = _make_deps(case=None)
    state = {"case_id": "case-x", "trace": [], "errors": []}
    delta = await load_case(state, deps=deps)
    # Missing case → no `case` key, error trace entry.
    assert "case" not in delta
    assert len(delta["trace"]) == 1
    assert "case_not_found" in delta["trace"][0]["error"]
    assert delta["errors"]
    assert "load_case" in delta["errors"][0]


async def test_load_case_swallows_repo_exception() -> None:
    deps = _make_deps(case_repo_raises=RuntimeError("db down"))
    state = {"case_id": "case-1", "trace": [], "errors": []}
    delta = await load_case(state, deps=deps)
    assert "case" not in delta
    assert "RuntimeError" in delta["trace"][0]["error"]
    assert delta["errors"]


# --- classify ---------------------------------------------------------


async def test_classify_populates_classification() -> None:
    case = _case(severity="high", rules=[_rule(), _rule(rule_type="overcharge")])
    deps = _make_deps(case=case)
    state = {"case_id": "case-1", "case": case, "trace": [], "errors": []}
    delta = await classify(state, deps=deps)
    cls = delta["classification"]
    assert cls["complexity"] == "high"
    assert cls["recommended_path"] == "deep"
    assert delta["trace"][-1]["node"] == "classify"
    assert delta["trace"][-1].get("error") is None
    # Metadata should expose the recommended_path so the worker can log
    # routing decisions without re-parsing classification.
    assert delta["trace"][-1]["metadata"]["recommended_path"] == "deep"


# --- analytics --------------------------------------------------------


async def test_analytics_calls_three_tools_concurrently_and_merges() -> None:
    case = _case(rules=[_rule(feature_name="discount_amt")])
    deps = _make_deps(
        case=case,
        pattern={"avg_amount": 50.0, "stddev_amount": 5.0,
                 "top_charge_codes": ["X"], "txn_count": 12, "settled_count": 10},
        rule_freq={"count": 9, "distinct_customers": 7, "top_evidences": []},
        drift={"snapshots": [
            {"feature_name": "discount_amt", "severity": "high",
             "ks_statistic": 0.21, "drift_detected": True},
        ]},
    )
    state = {"case_id": "case-1", "case": case, "trace": [], "errors": []}
    delta = await analytics(state, deps=deps)
    a = delta["analytics"]
    assert a["customer_pattern"]["txn_count"] == 12
    assert a["rule_type_frequency"]["count"] == 9
    assert a["drift_snapshot"]["snapshots"][0]["drift_detected"] is True
    deps.analytics_tools.customer_pattern_30d.assert_awaited_once_with("cust-1")
    deps.analytics_tools.rule_type_frequency.assert_awaited_once_with(
        "discount_mismatch"
    )
    deps.analytics_tools.drift_snapshot.assert_awaited_once_with(["discount_amt"])


async def test_analytics_skips_rule_freq_without_rules() -> None:
    case = _case(rules=[])
    deps = _make_deps(case=case)
    state = {"case_id": "case-1", "case": case, "trace": [], "errors": []}
    delta = await analytics(state, deps=deps)
    deps.analytics_tools.rule_type_frequency.assert_not_awaited()
    assert delta["analytics"]["rule_type_frequency"] == {"skipped": "no_rule_type"}
    deps.analytics_tools.customer_pattern_30d.assert_awaited_once()


# --- vector_search ----------------------------------------------------


async def test_vector_search_populates_retrieval() -> None:
    case = _case()
    deps = _make_deps(case=case)
    state = {"case_id": "case-1", "case": case, "trace": [], "errors": []}
    delta = await vector_search(state, deps=deps)
    assert isinstance(delta["retrieval"], list)
    assert delta["retrieval"][0]["case_id"] == "case-h-1"
    assert delta["trace"][-1].get("error") is None
    assert delta["trace"][-1]["metadata"]["hit_count"] == 1


# --- synthesize -------------------------------------------------------


async def test_synthesize_returns_ai_assist_shape() -> None:
    case = _case()
    deps = _make_deps(case=case)
    state = {
        "case_id": "case-1",
        "case": case,
        "classification": {
            "complexity": "medium",
            "evidence_strength": "moderate",
            "recommended_path": "deep",
        },
        "analytics": {
            "customer_pattern": {"txn_count": 20, "avg_amount": 75.0},
            "rule_type_frequency": {"count": 3, "distinct_customers": 2},
            "drift_snapshot": {"snapshots": []},
        },
        "retrieval": [
            {"case_id": "case-h-1", "score": 0.8,
             "disposition": "true_positive", "why_relevant": "match"}
        ],
        "trace": [],
        "errors": [],
    }
    delta = await synthesize(state, deps=deps)
    syn = delta["synthesis"]
    assert "summary" in syn
    assert syn["likelihood"] in (
        "true_positive", "false_positive", "needs_more_info"
    )
    assert 0.0 <= syn["confidence"] <= 1.0
    assert syn["rationale"]
    assert syn["recommended_steps"]
    assert syn["references"][0]["case_id"] == "case-h-1"


# --- per-node error preservation -------------------------------------


async def test_classify_failure_records_error_and_keeps_state() -> None:
    """A classifier exception must be swallowed at the node level —
    other state keys (case) survive unchanged into the next node.
    """
    raising_classify = AsyncMock(side_effect=ValueError("boom"))
    case = _case()
    deps = _make_deps(case=case, classify_fn=raising_classify)
    state = {"case_id": "case-1", "case": case, "trace": [], "errors": []}
    delta = await classify(state, deps=deps)
    # No `classification` because the classifier raised.
    assert "classification" not in delta
    # Error was recorded.
    assert delta["trace"][-1]["error"]
    assert "ValueError" in delta["trace"][-1]["error"]
    assert delta["errors"] and "classify" in delta["errors"][0]
    # `case` was set by an earlier node and the node didn't return
    # anything that would overwrite it — proving per-node errors
    # don't clobber surrounding state.
    assert "case" not in delta or delta.get("case") == case


# --- persist (calls persist_fn) --------------------------------------


async def test_persist_invokes_persist_fn() -> None:
    persist_fn = AsyncMock(return_value=None)
    case = _case()
    deps = _make_deps(case=case, persist_fn=persist_fn)
    state = {
        "case_id": "case-1",
        "case": case,
        "synthesis": {"summary": "x"},
        "trace": [{"node": "load_case",
                   "started_at": "2024-01-01T00:00:00+00:00",
                   "finished_at": "2024-01-01T00:00:00.001+00:00",
                   "duration_ms": 1.0, "output_keys": ["case"], "error": None,
                   "metadata": {}}],
        "errors": [],
    }
    delta = await persist(state, deps=deps)
    persist_fn.assert_awaited_once()
    args, _ = persist_fn.call_args
    assert args[0] == "case-1"
    assert args[1] == {"summary": "x"}
    assert delta["trace"][-1]["node"] == "persist"
    assert delta["trace"][-1].get("error") is None
