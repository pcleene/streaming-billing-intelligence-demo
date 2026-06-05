"""Tests for assist-agent state types + reducer wiring."""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, get_type_hints

import pytest

from app.services.assist_agent.state import (
    AgentTraceEntry,
    AssistAgentRunSummary,
    AssistAgentState,
    CaseClassification,
)


def test_agent_trace_entry_round_trips_through_dump_validate():
    started = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 5, 7, 12, 0, 1, tzinfo=timezone.utc)
    entry = AgentTraceEntry(
        node="classify",
        started_at=started,
        finished_at=finished,
        duration_ms=1234.5,
        output_keys=["classification"],
        error=None,
        metadata={"recommended_path": "deep"},
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["node"] == "classify"
    assert dumped["duration_ms"] == 1234.5
    assert dumped["metadata"] == {"recommended_path": "deep"}
    rebuilt = AgentTraceEntry.model_validate(dumped)
    assert rebuilt == entry


def test_assist_agent_state_typed_dict_has_expected_keys():
    # `total=False` so all keys are optional; we just verify the schema
    # advertises every key the graph reads/writes.
    hints = get_type_hints(AssistAgentState, include_extras=True)
    expected = {
        "case_id",
        "case",
        "classification",
        "analytics",
        "retrieval",
        "synthesis",
        "trace",
        "errors",
    }
    assert expected.issubset(set(hints.keys()))


def test_state_trace_uses_operator_add_reducer_annotation():
    """Confirm `trace` and `errors` carry the LangGraph `operator.add`
    reducer annotation — this is what lets nodes return
    `{"trace": [entry]}` and have the runtime APPEND across nodes
    instead of clobbering."""
    hints = get_type_hints(AssistAgentState, include_extras=True)
    for key in ("trace", "errors"):
        ann = hints[key]
        # `Annotated[list[...], operator.add]` exposes its metadata at
        # `__metadata__` on the typing form.
        assert hasattr(ann, "__metadata__"), f"{key} not Annotated"
        assert operator.add in ann.__metadata__, (
            f"{key} missing operator.add reducer"
        )


def test_simulated_state_merge_appends_via_reducer():
    """Sanity-check the reducer pattern by hand-merging two deltas the
    way LangGraph would. Each node returns a single-entry list and the
    reducer appends across calls."""
    s: dict = {"trace": [], "errors": []}

    def _merge(state: dict, delta: dict) -> dict:
        merged = dict(state)
        for k, v in delta.items():
            if k in ("trace", "errors") and isinstance(v, list):
                merged[k] = list(merged.get(k) or []) + list(v)
            else:
                merged[k] = v
        return merged

    s = _merge(s, {"trace": [{"node": "load_case"}], "errors": []})
    s = _merge(s, {"trace": [{"node": "classify"}]})
    s = _merge(s, {"trace": [{"node": "synthesize"}], "errors": ["boom"]})
    assert [t["node"] for t in s["trace"]] == [
        "load_case",
        "classify",
        "synthesize",
    ]
    assert s["errors"] == ["boom"]


def test_case_classification_round_trips():
    c = CaseClassification(
        complexity="high",
        evidence_strength="strong",
        recommended_path="deep",
        rationale="multi-rule critical case",
    )
    assert c.model_dump()["recommended_path"] == "deep"
    rebuilt = CaseClassification.model_validate(c.model_dump())
    assert rebuilt == c


def test_assist_agent_run_summary_round_trips():
    summary = AssistAgentRunSummary(
        case_id="QC-1",
        completed_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        total_duration_ms=42.5,
        nodes_executed=["load_case", "classify", "vector_search"],
        error_count=0,
        path_taken="fast",
    )
    dumped = summary.model_dump(mode="json")
    rebuilt = AssistAgentRunSummary.model_validate(dumped)
    assert rebuilt.case_id == "QC-1"
    assert rebuilt.path_taken == "fast"
    assert rebuilt.nodes_executed == ["load_case", "classify", "vector_search"]
