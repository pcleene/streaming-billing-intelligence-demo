"""LangGraph wiring for the AI-assist agent.

Builds a `StateGraph(AssistAgentState)` over the nodes in `nodes.py`,
adds a conditional edge after `classify` that routes:

  * "fast"  -> vector_search    (skip analytics)
  * "deep"  -> analytics -> vector_search

`load_case` failure is detected post-hoc via a conditional edge: if
`state['case']` is unset, jump straight to `END` — no point spending an
LLM call to classify a missing case.

`deps` is closure-captured when the graph is built so each node sees the
right `AssistAgentDeps` without relying on LangGraph's runtime config —
simpler signature for tests, no `RunnableConfig` plumbing.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from . import nodes as _nodes
from .nodes import AssistAgentDeps
from .state import AssistAgentState


def _route_after_load(state: AssistAgentState) -> str:
    """If `load_case` couldn't fetch the case, short-circuit to END.

    Otherwise hand off to `load_live_state` which hydrates the rich
    `customers_residential` + V3 `transactions` docs in parallel.
    """
    return "load_live_state" if state.get("case") else "__end__"


def _route_after_classify(state: AssistAgentState) -> str:
    """Pick fast vs deep based on classification.

    Defaults to "deep" when classification is missing entirely (e.g.
    classifier raised) — better to spend the analytics cycles than to
    silently degrade the synthesizer's grounding.
    """
    classification = state.get("classification") or {}
    path = classification.get("recommended_path")
    if path == "fast":
        return "vector_search"
    return "analytics"


def build_graph(deps: AssistAgentDeps) -> Any:
    """Build + compile the agent graph with `deps` closure-captured.

    Each node is wrapped so the LangGraph-facing signature is
    `async def(state) -> dict`, while the underlying node still takes
    the explicit `deps` keyword (which keeps node-level tests trivial).
    """

    def _wrap(fn):
        async def wrapped(state: AssistAgentState) -> dict:
            return await fn(state, deps=deps)

        wrapped.__name__ = fn.__name__
        return wrapped

    graph: StateGraph = StateGraph(AssistAgentState)
    graph.add_node("load_case", _wrap(_nodes.load_case))
    graph.add_node("load_live_state", _wrap(_nodes.load_live_state))
    graph.add_node("classify", _wrap(_nodes.classify))
    graph.add_node("analytics", _wrap(_nodes.analytics))
    graph.add_node("vector_search", _wrap(_nodes.vector_search))
    graph.add_node("synthesize", _wrap(_nodes.synthesize))
    graph.add_node("persist", _wrap(_nodes.persist))

    graph.add_edge(START, "load_case")
    graph.add_conditional_edges(
        "load_case",
        _route_after_load,
        {"load_live_state": "load_live_state", "__end__": END},
    )
    graph.add_edge("load_live_state", "classify")
    graph.add_conditional_edges(
        "classify",
        _route_after_classify,
        {"vector_search": "vector_search", "analytics": "analytics"},
    )
    graph.add_edge("analytics", "vector_search")
    graph.add_edge("vector_search", "synthesize")
    graph.add_edge("synthesize", "persist")
    graph.add_edge("persist", END)

    return graph.compile()


class AssistAgent:
    """Public entry point for the LangGraph agent.

    F2's worker constructs an `AssistAgentDeps` and calls
    `await AssistAgent(deps).run(case_id=...)` once per case event.
    """

    def __init__(self, deps: AssistAgentDeps) -> None:
        self._deps = deps
        self._graph = build_graph(deps)

    @property
    def graph(self) -> Any:
        """Expose the compiled graph for tests / introspection."""
        return self._graph

    async def run(self, *, case_id: str) -> AssistAgentState:
        """Invoke the graph with a fresh state seed.

        `trace` and `errors` start empty so the `operator.add` reducers
        have something to append to. The compiled graph returns the
        final merged state which the worker (F2) projects onto the
        case doc.
        """
        initial: AssistAgentState = {
            "case_id": case_id,
            "trace": [],
            "errors": [],
        }
        return await self._graph.ainvoke(initial)
