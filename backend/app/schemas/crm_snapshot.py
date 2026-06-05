"""CRM snapshot schemas (PR-1).

`crm_snapshots` records frozen point-in-time projections of the
authoritative customer record from the source CRM. Used for the
before/after panel (PR-12) and for diagnostic comparisons in cases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceLagBand = Literal["low", "medium", "high", "critical"]
DeltaSeverity = Literal["would_have_caused_quarantine", "normal"]


class FieldStaleness(BaseModel):
    field: str
    as_of: datetime
    staleness_hours: int


class DiffVsCurrent(BaseModel):
    added_promotions: list[str] = Field(default_factory=list)
    removed_promotions: list[str] = Field(default_factory=list)
    added_entitlements: list[str] = Field(default_factory=list)
    removed_entitlements: list[str] = Field(default_factory=list)
    delta_count: int = 0
    delta_severity: DeltaSeverity = "normal"


class CrmSnapshotDocument(BaseModel):
    """Persisted shape for `crm_snapshots`."""
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    snapshot_id: str
    customer_id: str

    snapshot_at: datetime
    source_system: str
    source_extraction_method: str
    source_lag_seconds: int = 0
    source_lag_band: SourceLagBand = "low"

    snapshot_completeness_score: float = Field(default=1.0, ge=0.0, le=1.0)
    fields_present: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    fields_stale: list[FieldStaleness] = Field(default_factory=list)

    customer_projection: dict = Field(default_factory=dict)
    diff_vs_current: DiffVsCurrent = Field(default_factory=DiffVsCurrent)

    used_in_cases: list[str] = Field(default_factory=list)

    created_at: datetime
