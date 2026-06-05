"""Schemas for the before/after panel (Phase B.7).

Two projections of the same customer + case, framed for the demo's
'Mongo stream vs Redshift batch' narrative:
  - `before`: how an analyst would have seen the world via a 24h-stale
    warehouse snapshot. Source of truth is `crm_snapshots` if present,
    otherwise we derive a stale view from the live customer doc by
    masking out items added in the last 24h.
  - `after`: live operational data — the embedded customer doc as it is
    *right now*, including ai_assist if the analyst has invoked it.

The service is read-only; it does not mutate any collection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProjectionSource = Literal["crm_snapshot", "derived_lag", "live"]


class BeforeAfterProjection(BaseModel):
    """One side of the comparison (left = before, right = after)."""

    model_config = ConfigDict(populate_by_name=True)

    source: ProjectionSource
    snapshot_at: datetime | None = None
    customer_summary: dict[str, Any] = Field(default_factory=dict)
    open_case_count: int = 0
    recent_transaction_count: int = 0
    has_ai_assist: bool = False
    ai_assist_summary: str | None = None
    rules_visible: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BeforeAfter(BaseModel):
    """Top-level response for `GET /api/cases/{case_id}/before-after`."""

    case_id: str
    customer_id: str
    generated_at: datetime
    before: BeforeAfterProjection
    after: BeforeAfterProjection
    would_quarantine: bool = Field(
        default=False,
        description="Whether the rule pack *now* would have caught this case "
                    "given the snapshot-era customer state.",
    )
    auto_resolution_latency_seconds: float | None = Field(
        default=None,
        description="If the case is resolved, seconds between case creation "
                    "and resolution. Highlights how the live platform "
                    "shortens analyst feedback loops.",
    )
