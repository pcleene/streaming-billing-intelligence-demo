"""LangGraph-driven AI-assist agent package.

Public surface:
  * `AssistAgent` — the runnable wrapper the worker (F2) drives.
  * `build_graph` — exposed for direct unit testing of the graph.
  * `AssistAgentState` / `AgentTraceEntry` / `CaseClassification` /
    `AssistAgentRunSummary` — typing for callers that consume the
    final state (notably the worker's persistence step).
  * `AssistAgentDeps` / `AnalyticsTools` — the dep-injection surface;
    F2 wires real repos + LLM, F3 ships the concrete `AnalyticsTools`.
"""

from .defaults import default_classifier, default_synthesizer
from .graph import AssistAgent, build_graph
from .nodes import AnalyticsTools, AssistAgentDeps
from .state import (
    AgentTraceEntry,
    AssistAgentRunSummary,
    AssistAgentState,
    CaseClassification,
)
from .tools import (
    CustomerPatternTool,
    DriftSnapshotTool,
    RuleTypeFrequencyTool,
    VectorSearchTool,
)

# `build_agent_graph` is the originally-spec'd name kept as a stable
# alias for `build_graph` so callers from the spec doc keep working.
build_agent_graph = build_graph

__all__ = [
    "AssistAgent",
    "AssistAgentDeps",
    "AssistAgentState",
    "AgentTraceEntry",
    "AnalyticsTools",
    "AssistAgentRunSummary",
    "CaseClassification",
    "CustomerPatternTool",
    "DriftSnapshotTool",
    "RuleTypeFrequencyTool",
    "VectorSearchTool",
    "build_agent_graph",
    "build_graph",
    "default_classifier",
    "default_synthesizer",
]
