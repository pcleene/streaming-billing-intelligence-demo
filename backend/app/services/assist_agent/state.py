"""State container + trace entry for the LangGraph-driven AI-assist agent.

The graph is event-driven (kicked off by `assist_agent_worker` on case
open / status transition). Every node returns a *partial state delta*
which LangGraph merges back into the running `AssistAgentState`. Errors
in any single node are recorded into `state['trace']` with an `error`
field and `state['errors']` so the conditional edges + downstream nodes
can decide whether to fall back / degrade gracefully.

Both `trace` and `errors` use the LangGraph `Annotated[list,
operator.add]` reducer pattern: each node returns
`{"trace": [entry]}` and the runtime appends to the running list — no
node has to know what other nodes already wrote.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class AgentTraceEntry(BaseModel):
    """One node-execution record persisted on the case alongside ai_assist.

    The worker (F2) projects `state['trace']` onto
    `quarantine_cases.ai_assist.agent_trace` so analysts can inspect
    which nodes ran, how long they took, and which (if any) errored.
    """

    model_config = ConfigDict(populate_by_name=True)

    node: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    output_keys: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseClassification(BaseModel):
    """Structured output of the `classify` node.

    Drives the conditional edge that routes "fast" cases past the
    analytics pipeline straight into vector search.
    """

    complexity: str  # "low" | "medium" | "high"
    evidence_strength: str  # "weak" | "moderate" | "strong"
    recommended_path: str  # "fast" | "deep"
    rationale: str = ""


class AssistAgentState(TypedDict, total=False):
    """LangGraph state dict shared across all nodes.

    `total=False` so each node returns a *delta* — only the keys it
    actually mutated — and LangGraph merges. `trace` and `errors` use
    `operator.add` reducers so node deltas are appended.
    """

    case_id: str
    case: dict | None
    # Live, rich source-of-truth docs hydrated by `load_live_state` so
    # downstream nodes (synthesizer, prompts) can ground in the full
    # customer 360 / transaction V3 doc rather than the case snapshot.
    customer: dict | None
    transaction: dict | None
    classification: dict | None  # serialised CaseClassification
    analytics: dict | None  # {customer_pattern, rule_type_freq, drift_snapshot}
    retrieval: list[dict] | None  # vector search hits
    synthesis: dict | None  # final AiAssist payload (dict)
    trace: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]


class AssistAgentRunSummary(BaseModel):
    """Compact summary of one agent run, serialised by the worker (F2).

    The full trace lives on the case doc; this summary lives on the
    worker's audit log so we can graph latency / error-rate without
    touching the case collection.
    """

    model_config = ConfigDict(populate_by_name=True)

    case_id: str
    completed_at: datetime
    total_duration_ms: float
    nodes_executed: list[str] = Field(default_factory=list)
    error_count: int = 0
    path_taken: str  # "fast" | "deep"
