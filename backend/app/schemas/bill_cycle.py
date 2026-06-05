"""Bill cycle schemas (PR-1; first-class collection in PR-4).

`bill_cycles` is the cycle anchor of record. Transactions embed the cycle_id
at write time (PR-3). Quarantine cases reference it. Rule and dashboard
panels read directly from the document — never via $lookup.

The cycle document is medium-rich: it carries the cycle anchors, status
timeline, expected vs billed breakdown, variance drivers, and a denormalised
preview of the previous cycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CycleStatus = Literal["open", "billed", "closed", "voided"]


class CycleAnchors(BaseModel):
    cycle_start: datetime
    cycle_end: datetime
    bill_day_of_month: int = Field(ge=1, le=31)
    cycle_length_days: int = Field(ge=1, le=62)
    tz: str = "Asia/Kuala_Lumpur"


class StatusTimelineEntry(BaseModel):
    at: datetime
    status: CycleStatus
    by: str  # actor / system component name


class BillBreakdown(BaseModel):
    """Sum-decomposition of the cycle bill.

    Used in two places: `expected_breakdown` (what the system thinks
    *should* be billed at cycle open) and `billed_breakdown` (what was
    actually billed once transactions are settled).
    """
    subscription_myr: float = 0.0
    addons_myr: float = 0.0
    ppv_myr: float = 0.0
    device_fees_myr: float = 0.0
    discounts_myr: float = 0.0
    tax_myr: float = 0.0
    other_fees_myr: float = 0.0


class VarianceDriver(BaseModel):
    """A single field-level explanation of expected-vs-billed delta."""
    field: str
    expected: float
    billed: float
    delta_myr: float
    explanation: str


class PreviousCycleSummary(BaseModel):
    """Denormalised preview of the prior cycle for trend rendering."""
    cycle_id: str
    billed_amount_myr: float
    variance_myr: float
    had_quarantine: bool


class CycleAudit(BaseModel):
    billing_run_id: str
    generated_at: datetime
    generator: str  # e.g. "billing_engine_v2", "manual_admin"


class BillCycleDocument(BaseModel):
    """Persisted shape for `bill_cycles`."""
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    cycle_id: str
    customer_id: str
    customer_type: Literal["residential", "commercial"]
    parent_account_id: str | None = None
    account_id: str

    cycle_anchors: CycleAnchors
    status_timeline: list[StatusTimelineEntry] = Field(default_factory=list)
    current_status: CycleStatus = "open"

    expected_amount_myr: float = 0.0
    expected_breakdown: BillBreakdown = Field(default_factory=BillBreakdown)
    billed_amount_myr: float = 0.0
    billed_breakdown: BillBreakdown = Field(default_factory=BillBreakdown)
    variance_myr: float = 0.0
    variance_pct: float = 0.0
    variance_drivers: list[VarianceDriver] = Field(default_factory=list)

    transaction_count: int = 0
    transaction_total_myr: float = 0.0
    transaction_ids: list[str] = Field(default_factory=list)

    promotions_applied_in_cycle: list[dict] = Field(default_factory=list)
    promotions_expected_in_cycle: list[dict] = Field(default_factory=list)

    associated_quarantine_case_ids: list[str] = Field(default_factory=list)
    previous_cycle_summary: PreviousCycleSummary | None = None

    audit: CycleAudit | None = None

    created_at: datetime
    updated_at: datetime
