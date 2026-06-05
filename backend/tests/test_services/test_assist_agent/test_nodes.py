"""Tests for the individual assist-agent nodes (no LangGraph runtime)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.assist_agent.nodes import (
    AssistAgentDeps,
    analytics,
    classify,
    load_case,
    load_live_state,
    persist,
    synthesize,
    vector_search,
)


# --- Helpers ----------------------------------------------------------


@dataclass
class _FakeAnalyticsTools:
    """Plain async-fns + call-tracking, easier than a Protocol-conforming
    `MagicMock` (which doesn't auto-spec async methods reliably here)."""

    customer_pattern_30d: Any
    rule_type_frequency: Any
    drift_snapshot: Any


def _make_deps(**overrides: Any) -> AssistAgentDeps:
    """Build an AssistAgentDeps with AsyncMocks and let tests override."""
    case_repo = AsyncMock()
    case_repo.get_by_id = AsyncMock(
        return_value={
            "case_id": "QC-1",
            "customer_id": "CUST-1",
            "rules_triggered": [
                {"rule_type": "DUPLICATE_CHARGE", "evidence": {"feature_name": "f1"}},
                {"rule_type": "OVERCHARGE", "evidence": {"feature_name": "f2"}},
            ],
            "severity": "high",
        }
    )
    tools = _FakeAnalyticsTools(
        customer_pattern_30d=AsyncMock(return_value={"txn_count": 12}),
        rule_type_frequency=AsyncMock(return_value={"count": 3}),
        drift_snapshot=AsyncMock(return_value={"snapshots": []}),
    )

    async def _classify_fn(case: dict) -> dict:
        return {
            "complexity": "high",
            "evidence_strength": "strong",
            "recommended_path": "deep",
            "rationale": "ok",
        }

    async def _vector_search_fn(case: dict) -> list[dict]:
        return [{"case_id": "HIST-1", "score": 0.9}]

    async def _synthesize_fn(state: dict) -> dict:
        return {"summary": "ok", "likelihood": "true_positive"}

    async def _persist_fn(case_id: str, ai_assist: dict, trace: list[dict]) -> None:
        return None

    deps = AssistAgentDeps(
        case_repo=case_repo,
        classify_fn=_classify_fn,
        analytics_tools=tools,
        vector_search_fn=_vector_search_fn,
        synthesize_fn=_synthesize_fn,
        persist_fn=_persist_fn,
    )
    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps


# --- load_case --------------------------------------------------------


async def test_load_case_populates_case_from_repo():
    deps = _make_deps()
    out = await load_case({"case_id": "QC-1"}, deps=deps)
    assert out["case"]["case_id"] == "QC-1"
    assert any(t["node"] == "load_case" and t["error"] is None for t in out["trace"])


async def test_load_case_missing_case_records_error_no_case_key():
    deps = _make_deps()
    deps.case_repo.get_by_id = AsyncMock(return_value=None)
    out = await load_case({"case_id": "QC-MISSING"}, deps=deps)
    assert "case" not in out
    assert any(
        "case_not_found" in (t.get("error") or "") for t in out["trace"]
    )
    assert any("case_not_found" in e for e in out["errors"])


# --- load_live_state --------------------------------------------------


async def test_load_live_state_hydrates_customer_and_transaction():
    cust_svc = AsyncMock()
    cust_svc.get_profile = AsyncMock(
        return_value={"customer_id": "CUST-1", "tier": "Gold"}
    )
    txn_repo = AsyncMock()
    txn_repo.get_by_id = AsyncMock(
        return_value={"transaction_id": "TXN-1", "items": []}
    )
    deps = _make_deps(customer_360_service=cust_svc, transaction_repo=txn_repo)

    state = {
        "case": {
            "customer_id": "CUST-1",
            "transaction_summary": {"transaction_id": "TXN-1"},
        }
    }
    out = await load_live_state(state, deps=deps)
    assert out["customer"]["tier"] == "Gold"
    assert out["transaction"]["transaction_id"] == "TXN-1"
    cust_svc.get_profile.assert_awaited_once()
    txn_repo.get_by_id.assert_awaited_once()
    trace = out["trace"][0]
    assert trace["node"] == "load_live_state"
    assert trace["error"] is None
    assert set(trace["output_keys"]) == {"customer", "transaction"}


async def test_load_live_state_no_deps_wired_emits_skipped_trace_only():
    """When neither dep is wired (legacy / unit-test setups), the node
    is a one-shot no-op — single trace entry, no state delta."""
    deps = _make_deps()  # no customer_360_service / transaction_repo
    out = await load_live_state(
        {"case": {"customer_id": "CUST-1"}}, deps=deps
    )
    assert "customer" not in out
    assert "transaction" not in out
    trace = out["trace"][0]
    assert trace["node"] == "load_live_state"
    assert trace["metadata"].get("skipped") == "deps_not_wired"


async def test_load_live_state_partial_failure_keeps_other_result():
    """One side errors (transaction repo down), the other side still
    populates state — non-fatal partial-degradation pattern."""
    cust_svc = AsyncMock()
    cust_svc.get_profile = AsyncMock(
        return_value={"customer_id": "CUST-1"}
    )
    txn_repo = AsyncMock()
    txn_repo.get_by_id = AsyncMock(side_effect=RuntimeError("mongo down"))
    deps = _make_deps(customer_360_service=cust_svc, transaction_repo=txn_repo)

    state = {
        "case": {
            "customer_id": "CUST-1",
            "transaction_summary": {"transaction_id": "TXN-1"},
        }
    }
    out = await load_live_state(state, deps=deps)
    assert out["customer"]["customer_id"] == "CUST-1"
    assert "transaction" not in out
    trace = out["trace"][0]
    assert trace["error"] and "mongo down" in trace["error"]
    assert any("load_live_state" in e for e in out.get("errors", []))


async def test_load_live_state_runs_concurrently():
    """Both fetches must run via `asyncio.gather` — two 50ms sleeps
    must finish in <80ms (well under the 100ms serial sum)."""
    cust_svc = AsyncMock()
    txn_repo = AsyncMock()

    async def _slow_cust(*_a: Any, **_kw: Any) -> dict:
        await asyncio.sleep(0.05)
        return {"customer_id": "CUST-1"}

    async def _slow_txn(*_a: Any, **_kw: Any) -> dict:
        await asyncio.sleep(0.05)
        return {"transaction_id": "TXN-1"}

    cust_svc.get_profile = AsyncMock(side_effect=_slow_cust)
    txn_repo.get_by_id = AsyncMock(side_effect=_slow_txn)

    deps = _make_deps(customer_360_service=cust_svc, transaction_repo=txn_repo)
    state = {
        "case": {
            "customer_id": "CUST-1",
            "transaction_summary": {"transaction_id": "TXN-1"},
        }
    }
    t0 = time.perf_counter()
    out = await load_live_state(state, deps=deps)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.08, f"load_live_state ran serially? {elapsed:.3f}s"
    assert "customer" in out and "transaction" in out


# --- classify ---------------------------------------------------------


async def test_classify_calls_classify_fn_and_stores_dict():
    classify_fn = AsyncMock(
        return_value={"recommended_path": "fast", "complexity": "low"}
    )
    deps = _make_deps(classify_fn=classify_fn)
    state = {"case": {"case_id": "QC-1"}}
    out = await classify(state, deps=deps)
    classify_fn.assert_awaited_once()
    assert out["classification"]["recommended_path"] == "fast"
    assert out["trace"][0]["node"] == "classify"
    assert out["trace"][0]["error"] is None


async def test_classify_swallows_exception_and_emits_error_trace():
    async def _boom(case: dict) -> dict:
        raise RuntimeError("LLM 500")

    deps = _make_deps(classify_fn=_boom)
    out = await classify({"case": {"case_id": "QC-1"}}, deps=deps)
    # Did NOT raise — graph keeps moving.
    assert "classification" not in out
    assert out["trace"][0]["error"] is not None
    assert "LLM 500" in out["trace"][0]["error"]
    assert any("classify" in e for e in out["errors"])


# --- analytics --------------------------------------------------------


async def test_analytics_calls_all_three_tools_concurrently():
    """Measure wall-clock: three tools each sleeping 50ms must finish
    in <130ms total (well under the 150ms serial sum) — proves
    `asyncio.gather` ran them concurrently."""

    async def _slow(value: dict):
        async def _inner(*_a: Any, **_kw: Any) -> dict:
            await asyncio.sleep(0.05)
            return value

        return _inner

    tools = _FakeAnalyticsTools(
        customer_pattern_30d=AsyncMock(side_effect=lambda *_a, **_kw: asyncio.sleep(0.05) or {}),
        rule_type_frequency=AsyncMock(side_effect=lambda *_a, **_kw: asyncio.sleep(0.05) or {}),
        drift_snapshot=AsyncMock(side_effect=lambda *_a, **_kw: asyncio.sleep(0.05) or {}),
    )

    # AsyncMock with side_effect that returns a coroutine + dict. Use
    # side_effect functions that actually `await` sleep.
    async def _sleep_pattern(*_a: Any, **_kw: Any) -> dict:
        await asyncio.sleep(0.05)
        return {"txn_count": 1}

    async def _sleep_rule(*_a: Any, **_kw: Any) -> dict:
        await asyncio.sleep(0.05)
        return {"count": 1}

    async def _sleep_drift(*_a: Any, **_kw: Any) -> dict:
        await asyncio.sleep(0.05)
        return {"snapshots": []}

    tools.customer_pattern_30d = AsyncMock(side_effect=_sleep_pattern)
    tools.rule_type_frequency = AsyncMock(side_effect=_sleep_rule)
    tools.drift_snapshot = AsyncMock(side_effect=_sleep_drift)
    deps = _make_deps()
    deps.analytics_tools = tools

    state = {
        "case": {
            "customer_id": "CUST-1",
            "rules_triggered": [
                {"rule_type": "DUP", "evidence": {"feature_name": "f1"}}
            ],
        }
    }
    t0 = time.perf_counter()
    out = await analytics(state, deps=deps)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.13, f"analytics ran serially? {elapsed:.3f}s"
    tools.customer_pattern_30d.assert_awaited_once()
    tools.rule_type_frequency.assert_awaited_once()
    tools.drift_snapshot.assert_awaited_once()
    assert "customer_pattern" in out["analytics"]
    assert "rule_type_frequency" in out["analytics"]
    assert "drift_snapshot" in out["analytics"]


async def test_analytics_partial_failure_keeps_other_results():
    deps = _make_deps()
    tools = deps.analytics_tools
    tools.customer_pattern_30d = AsyncMock(return_value={"txn_count": 7})
    tools.rule_type_frequency = AsyncMock(side_effect=RuntimeError("mongo down"))
    tools.drift_snapshot = AsyncMock(return_value={"snapshots": ["s1"]})
    state = {
        "case": {
            "customer_id": "CUST-1",
            "rules_triggered": [
                {"rule_type": "DUP", "evidence": {"feature_name": "f1"}}
            ],
        }
    }
    out = await analytics(state, deps=deps)
    assert out["analytics"]["customer_pattern"] == {"txn_count": 7}
    assert "error" in out["analytics"]["rule_type_frequency"]
    assert out["analytics"]["drift_snapshot"] == {"snapshots": ["s1"]}
    # Trace records the partial-failure on the analytics entry itself.
    trace_entry = out["trace"][0]
    assert trace_entry["node"] == "analytics"
    assert trace_entry["error"] and "mongo down" in trace_entry["error"]
    # But the node still produced its analytics payload — the graph
    # keeps moving toward synthesize.
    assert out.get("errors")


# --- vector_search ----------------------------------------------------


async def test_vector_search_populates_retrieval():
    vfn = AsyncMock(return_value=[{"case_id": "HIST-1", "score": 0.92}])
    deps = _make_deps(vector_search_fn=vfn)
    out = await vector_search({"case": {"case_id": "QC-1"}}, deps=deps)
    vfn.assert_awaited_once()
    assert out["retrieval"] == [{"case_id": "HIST-1", "score": 0.92}]
    assert out["trace"][0]["node"] == "vector_search"


# --- synthesize -------------------------------------------------------


async def test_synthesize_calls_synthesize_fn_and_stores_result():
    sfn = AsyncMock(return_value={"summary": "x", "likelihood": "true_positive"})
    deps = _make_deps(synthesize_fn=sfn)
    state = {"case": {"case_id": "QC-1"}, "classification": {}, "retrieval": []}
    out = await synthesize(state, deps=deps)
    sfn.assert_awaited_once()
    assert out["synthesis"] == {"summary": "x", "likelihood": "true_positive"}


# --- persist ----------------------------------------------------------


async def test_persist_calls_persist_fn_with_right_args():
    pfn = AsyncMock(return_value=None)
    deps = _make_deps(persist_fn=pfn)
    state = {
        "case_id": "QC-1",
        "synthesis": {"summary": "ok"},
        "trace": [{"node": "load_case"}],
    }
    out = await persist(state, deps=deps)
    pfn.assert_awaited_once_with(
        "QC-1", {"summary": "ok"}, [{"node": "load_case"}]
    )
    assert out["trace"][0]["node"] == "persist"
