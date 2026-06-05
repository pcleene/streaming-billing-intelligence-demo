"""Quarantine case schemas — live + historical (RAG corpus)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import CaseStatus, Disposition, Severity, TimestampedModel
from .customer import CustomerSegment


class RuleHit(BaseModel):
    """One rule's contribution to a case."""
    rule_id: str | None = None
    rule_type: str
    rule_name: str
    severity: Severity
    evidence: dict = Field(default_factory=dict)


class CustomerSnapshot(BaseModel):
    """Frozen customer context at quarantine time."""
    customer_id: str
    name: str
    segment: CustomerSegment
    active_promotions: list[dict] = Field(default_factory=list)
    active_subscriptions: list[dict] = Field(default_factory=list)


# --- Phase B.4 — standardized ai_assist shape ---------------------------

AssistLikelihood = Literal["true_positive", "false_positive", "needs_more_info"]


class AiAssistCitation(BaseModel):
    """One historical case the LLM grounded its recommendation on."""
    case_id: str
    disposition: str
    score: float | None = None
    why_relevant: str | None = None


class AiAssistRetrieval(BaseModel):
    """Provenance for the vector-search step that fed the LLM call."""
    index_name: str
    k: int
    score_threshold: float
    retrieved_case_ids: list[str] = Field(default_factory=list)
    embedding_model: str | None = None


class AgentTraceEntry(BaseModel):
    """Per-node execution record from the LangGraph AI-assist agent.

    Defined inline here (mirroring the canonical shape in
    `app.services.assist_agent.state.AgentTraceEntry`) so the schemas
    module stays free of service-layer imports — otherwise the
    services package, which depends on schemas, would form a cycle.

    `error` distinguishes ok-vs-error — when it's None the node ran
    cleanly, when it's a string it carries the exception summary.
    `output_keys` lists the state keys the node wrote (small projection
    so the trace stays compact), `metadata` is a free-form bucket for
    node-specific context (e.g. classifier's `recommended_path`).
    """
    node: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    output_keys: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class AiAssist(BaseModel):
    """Locked-down AI-assist payload persisted on `quarantine_cases.ai_assist`.

    Phase B.4 swaps the legacy free-form `dict | None` for this schema so
    the UI can rely on a stable shape and the validator can enforce
    required fields.
    """
    model_config = ConfigDict(populate_by_name=True)

    summary: str
    likelihood: AssistLikelihood
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: list[str] = Field(default_factory=list, min_length=1)
    recommended_steps: list[str] = Field(default_factory=list, min_length=1)
    references: list[AiAssistCitation] = Field(default_factory=list)
    retrieval: AiAssistRetrieval | None = None
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    model: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None
    # PR-AG: per-node trace from the LangGraph agent path. Stored as a
    # plain list-of-dict so the worker (F2) can pass `state['trace']`
    # straight through without a re-validation step. Optional /
    # additive — PR-8's linear-flow tests + payloads remain valid
    # unchanged because this defaults to an empty list.
    agent_trace: list[dict] = Field(default_factory=list)


class QuarantineCase(TimestampedModel):
    """Live case in the queue."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    case_id: str
    customer_id: str
    transaction_id: str | None = None  # null for window-aggregate cases
    severity: Severity
    status: CaseStatus = CaseStatus.OPEN

    rules_triggered: list[RuleHit]
    customer_snapshot: CustomerSnapshot | None = None

    # Set by analyst or AI assist (B.4: locked down to AiAssist).
    ai_assist: AiAssist | None = None
    analyst_notes: str | None = None
    disposition: Disposition | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class EmbedSource(BaseModel):
    """AutoEmbed source object on history docs.

    The Atlas Auto Embedding index points at the `embed_source.text`
    leaf string (depth-1 string under a single object). Atlas reads
    this string and stores the vector invisibly — application code
    never sees the vector. See ADR-032 and
    `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for the
    pre-AutoEmbed shape.
    """
    text: str = Field(..., min_length=1)


class QuarantineCaseHistory(BaseModel):
    """Historical resolved case — the RAG corpus.

    AutoEmbed (ADR-032): the source text lives on `embed_source.text`
    and Atlas manages the vector internally.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    case_id: str
    customer_id: str
    customer_segment: CustomerSegment
    rules_triggered: list[RuleHit]
    transaction_summary: dict
    customer_context_summary: dict

    # Investigation outcome
    disposition: Disposition
    severity: Severity
    analyst_notes: str
    resolution_summary: str
    resolved_at: datetime

    # AutoEmbed source (Atlas-managed vector, see ADR-032)
    embed_source: EmbedSource


# =========================================================================
#       PR-1 / PR-7 / PR-8 — Rich quarantine case + history shapes
# =========================================================================
# These extend (not replace) the lean shapes above. The PR-7 case lifecycle
# worker writes the rich shape; the existing minimal shape continues to be
# accepted by the validator at validationLevel=moderate.

PriorityBand = Literal["P0", "P1", "P2", "P3"]
ActorType = Literal["system", "analyst", "ai"]
HistoryDispositionStr = Literal[
    "false_positive", "true_positive_refund",
    "true_positive_recharge", "needs_human", "duplicate",
]


class LifecycleEvent(BaseModel):
    """One status transition on the case timeline."""
    at: datetime
    from_status: str | None = None
    to_status: str
    actor_type: ActorType
    actor_id: str
    rule_types_fired: list[str] | None = None
    note: str | None = None


class ItemSummary(BaseModel):
    name: str
    category: str | None = None
    unit_price_myr: float = 0.0
    discount_per_unit_myr: float = 0.0
    line_total_myr: float = 0.0
    promo_id: str | None = None
    charge_code: str


class TransactionSummary(BaseModel):
    """Frozen transaction snapshot embedded on the case at open.

    Populated from `TransactionDocumentV3` fields plus a short items_summary
    so the analyst tile renders without a join."""
    transaction_id: str
    timestamp: datetime
    transaction_type: str
    channel: str | None = None
    subtotal_myr: float = 0.0
    total_discount_myr: float = 0.0
    tax_myr: float = 0.0
    total_myr: float = 0.0
    items_count: int = 0
    items_summary: list[ItemSummary] = Field(default_factory=list)
    payment_method: str | None = None
    card_last4: str | None = None


class CustomerSnapshotV3(BaseModel):
    """Frozen customer context at quarantine time (rich variant).

    Replaces the lean `CustomerSnapshot` above for PR-7+. Both coexist in
    the collection during transition."""
    customer_id: str
    name: str
    tier: str | None = None
    ic_number: str | None = None
    loyalty_member_id: str | None = None
    package_at_billing: str | None = None
    active_promotions_at_billing: list[dict] = Field(default_factory=list)
    active_entitlements_at_billing: list[str] = Field(default_factory=list)
    lifetime_quarantine_count: int = 0
    tenure_months: int = 0
    churn_risk: float = 0.0
    service_state: str | None = None


class SimilarCasePreview(BaseModel):
    case_id: str
    relevance: float = Field(ge=0.0, le=1.0)
    disposition: str
    resolved_at: datetime
    summary: str


class Sla(BaseModel):
    """Per-case SLA tracking. PR-7's `case_lifecycle_worker` sweeps every
    minute updating `elapsed_minutes` / `is_breached`."""
    target_resolution_at: datetime
    elapsed_minutes: float = 0.0
    minutes_to_breach: float = 0.0
    is_breached: bool = False
    breached_at: datetime | None = None


class RevenueImpact(BaseModel):
    """Confidence-weighted revenue model for the case."""
    amount_at_risk_myr: float = 0.0
    revenue_protected_if_correct_myr: float = 0.0
    false_positive_ops_cost_myr: float = 0.0
    ev_resolution_value_myr: float = 0.0


class QuarantineCaseV3(TimestampedModel):
    """Rich live case (PR-7).

    On case open, every field is populated except analyst inputs and
    `ai_assist`. PR-8 wires `ai_assist` via `ai_assist_service`.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    case_id: str

    # Identity & routing
    customer_id: str
    customer_type: Literal["residential", "commercial"] | None = None
    account_id: str | None = None
    parent_account_id: str | None = None
    outlet_id: str | None = None
    transaction_id: str | None = None
    cycle_id: str | None = None
    entity: str | None = None

    # Severity & priority
    severity: Severity
    status: CaseStatus = CaseStatus.OPEN
    priority_score: float = 0.0
    priority_band: PriorityBand = "P3"
    auto_priority_drivers: list[str] = Field(default_factory=list)

    # Lifecycle timeline
    lifecycle: list[LifecycleEvent] = Field(default_factory=list)

    # Multi-rule trigger details (legacy `RuleHit` shape extended via
    # structured `evidence`).
    rules_triggered: list[RuleHit] = Field(default_factory=list)

    # Customer snapshot (frozen)
    customer_snapshot: CustomerSnapshotV3 | None = None

    # Transaction summary (denormalised)
    transaction_summary: TransactionSummary | None = None

    # AI assist (PR-8)
    ai_assist: AiAssist | None = None

    # Similar cases preview (top 3 from RAG, denormalised)
    similar_cases_preview: list[SimilarCasePreview] = Field(default_factory=list)

    # Analyst inputs
    analyst_notes: str | None = None
    disposition: Disposition | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    # SLA tracking
    sla: Sla | None = None

    # Revenue impact
    revenue_impact: RevenueImpact = Field(default_factory=RevenueImpact)

    # Tags
    tags: list[str] = Field(default_factory=list)


# --- History (RAG corpus) - rich -----------------------------------------

class Learnings(BaseModel):
    """LLM-readable learnings extracted at case resolution. Used as
    embedding input alongside the rule + transaction context."""
    pattern_name: str
    root_cause: str
    what_would_have_prevented: str
    false_positive_indicators: list[str] = Field(default_factory=list)
    true_positive_indicators: list[str] = Field(default_factory=list)
    confidence_in_learning: float = Field(default=0.0, ge=0.0, le=1.0)


class RagFeedback(BaseModel):
    positive: int = 0
    negative: int = 0
    neutral: int = 0


class QuarantineCaseHistoryV3(BaseModel):
    """Rich RAG corpus document (PR-8 + AutoEmbed PR-A).

    `_schema_version=4` documents use Atlas Auto Embedding: the source
    text lives on `embed_source.text` and Atlas manages the vector
    invisibly. See ADR-032 and
    `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for the
    pre-AutoEmbed (BYO Voyage) shape that this replaces.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    schema_version: int = Field(default=4, alias="_schema_version")
    case_id: str

    # Identity
    customer_id: str
    customer_type: Literal["residential", "commercial"] | None = None
    customer_tier: str | None = None
    transaction_id: str | None = None
    cycle_id: str | None = None
    entity: str | None = None

    # Frozen rule + transaction context
    rules_triggered: list[RuleHit] = Field(default_factory=list)
    transaction_summary: TransactionSummary | None = None
    customer_context_summary: dict = Field(default_factory=dict)

    # Outcome
    disposition: HistoryDispositionStr | Disposition
    severity: Severity
    analyst_id: str | None = None
    analyst_notes: str
    resolution_summary: str
    resolved_at: datetime
    resolution_time_minutes: int = 0
    resolution_path: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    compensation_to_customer_myr: float = 0.0

    # LLM-readable learnings
    learnings: Learnings | None = None

    # AutoEmbed source (Atlas-managed vector, ADR-032)
    embed_source: EmbedSource

    # Provenance & feedback
    used_in_rag_count: int = 0
    last_used_in_rag_at: datetime | None = None
    rag_relevance_feedback: RagFeedback = Field(default_factory=RagFeedback)
    similar_resolved_cases: list[str] = Field(default_factory=list)

    archived_at: datetime | None = None
