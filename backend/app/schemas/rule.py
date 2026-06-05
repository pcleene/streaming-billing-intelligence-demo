"""Rule schemas — asymmetric, polymorphic by rule_type (ADR-006).

Each rule_type has its own *Parameters model. The top-level rule document
uses a Pydantic discriminated union over `rule_type` so the right shape is
validated at the API boundary.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import RuleMode, Severity, TimestampedModel


class RuleType(str, Enum):
    DISCOUNT_MISMATCH            = "discount_mismatch"
    VELOCITY_ANOMALY             = "velocity_anomaly"
    AMOUNT_OUTLIER               = "amount_outlier"
    ENTITLEMENT_MISMATCH         = "entitlement_mismatch"
    GEOGRAPHIC_ANOMALY           = "geographic_anomaly"
    DUPLICATE_TRANSACTION        = "duplicate_transaction"
    # Phase B.2 — Acme-named rules
    TERMINATION_FEE_CHECK        = "termination_fee_check"
    UNEARNED_EARNED_SEGREGATION  = "unearned_earned_segregation"
    DOUBLE_CHARGE_MULTI_CODE     = "double_charge_multi_code"
    PRORATION_CHECK              = "proration_check"


# --- Per-type parameter shapes (asymmetric) --------------------------
class DiscountMismatchParams(BaseModel):
    rule_type: Literal[RuleType.DISCOUNT_MISMATCH] = RuleType.DISCOUNT_MISMATCH
    discount_field: str = "transaction.discount_amount"
    crm_promotion_field: str = "customer.active_promotions"
    grace_period_minutes: int = Field(default=60, ge=0, le=1440)
    min_discount_amount_myr: float = Field(default=0.0, ge=0)


class VelocityAnomalyParams(BaseModel):
    rule_type: Literal[RuleType.VELOCITY_ANOMALY] = RuleType.VELOCITY_ANOMALY
    window_seconds: int = Field(default=300, ge=10, le=3600)
    max_transactions: int = Field(default=5, ge=2)
    group_by: list[str] = Field(default_factory=lambda: ["customer_id", "merchant_id"])


class AmountOutlierParams(BaseModel):
    rule_type: Literal[RuleType.AMOUNT_OUTLIER] = RuleType.AMOUNT_OUTLIER
    std_dev_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    lookback_days: int = Field(default=90, ge=7, le=730)
    minimum_history_count: int = Field(default=10, ge=1)


class EntitlementMismatchParams(BaseModel):
    rule_type: Literal[RuleType.ENTITLEMENT_MISMATCH] = RuleType.ENTITLEMENT_MISMATCH
    content_id_field: str = "transaction.content_id"


class GeographicAnomalyParams(BaseModel):
    rule_type: Literal[RuleType.GEOGRAPHIC_ANOMALY] = RuleType.GEOGRAPHIC_ANOMALY
    home_state_field: str = "customer.address.state"
    allowed_distance_km: int = Field(default=500, ge=10)
    travel_grace_window_hours: int = Field(default=24, ge=0, le=168)


class DuplicateTransactionParams(BaseModel):
    rule_type: Literal[RuleType.DUPLICATE_TRANSACTION] = RuleType.DUPLICATE_TRANSACTION
    fields_to_match: list[str] = Field(
        default_factory=lambda: ["customer_id", "merchant_id", "amount"]
    )
    window_seconds: int = Field(default=60, ge=5, le=600)


# --- Phase B.2 — Acme-named rules ----------------------------------
class TerminationFeeCheckParams(BaseModel):
    """Termination fee charged but customer still active OR within grace.

    Fires when a transaction whose metadata.charge_code matches `charge_code`
    is observed for a customer whose subscription_status is `active`, or whose
    cancellation_at is within the last `grace_period_days`.
    """
    rule_type: Literal[RuleType.TERMINATION_FEE_CHECK] = RuleType.TERMINATION_FEE_CHECK
    charge_code: str = "CC_TERMINATION_FEE"
    min_amount_myr: float = Field(default=50.0, ge=0)
    grace_period_days: int = Field(default=30, ge=0, le=365)


class UnearnedEarnedSegregationParams(BaseModel):
    """Subscription/addon charge missing the earned/unearned split.

    Fires when a transaction of one of `applies_to_types` is missing
    `metadata.earned_amount_myr` + `metadata.unearned_amount_myr`, or when
    those two don't sum to `amount` within `tolerance_myr`.
    """
    rule_type: Literal[RuleType.UNEARNED_EARNED_SEGREGATION] = RuleType.UNEARNED_EARNED_SEGREGATION
    applies_to_types: list[str] = Field(
        default_factory=lambda: ["subscription_charge", "addon_purchase"]
    )
    tolerance_myr: float = Field(default=0.5, ge=0)


class DoubleChargeMultiCodeParams(BaseModel):
    """Same logical service charged twice via different charge_code values.

    Within `window_seconds`, when more than one transaction for the same
    `match_fields` tuple is observed, and at least two distinct charge_code
    values from `redundant_codes` appear, fire a duplicate-charge case.
    """
    rule_type: Literal[RuleType.DOUBLE_CHARGE_MULTI_CODE] = RuleType.DOUBLE_CHARGE_MULTI_CODE
    redundant_codes: list[str] = Field(
        default_factory=lambda: ["CC_PPV_DIRECT", "CC_BUNDLE_PPV"]
    )
    match_fields: list[str] = Field(
        default_factory=lambda: ["customer_id", "content_id"]
    )
    window_seconds: int = Field(default=600, ge=30, le=3600)


class ProrationCheckParams(BaseModel):
    """Mid-cycle subscription change without a matching proration adjustment.

    Fires on a transaction of `applies_to_types` flagged
    `metadata.is_mid_cycle_change == true` whose
    `metadata.proration_amount_myr` differs from `metadata.expected_proration_myr`
    by more than `tolerance_pct`.
    """
    rule_type: Literal[RuleType.PRORATION_CHECK] = RuleType.PRORATION_CHECK
    applies_to_types: list[str] = Field(
        default_factory=lambda: ["subscription_charge"]
    )
    tolerance_pct: float = Field(default=0.05, ge=0.0, le=1.0)


RuleParameters = Annotated[
    DiscountMismatchParams
    | VelocityAnomalyParams
    | AmountOutlierParams
    | EntitlementMismatchParams
    | GeographicAnomalyParams
    | DuplicateTransactionParams
    | TerminationFeeCheckParams
    | UnearnedEarnedSegregationParams
    | DoubleChargeMultiCodeParams
    | ProrationCheckParams,
    Field(discriminator="rule_type"),
]


# --- Rule document ----------------------------------------------------
class RuleMetrics(BaseModel):
    hit_count_24h: int = 0
    hit_count_total: int = 0
    last_hit_at: datetime | None = None
    false_positive_rate: float = 0.0  # populated as analysts mark cases legitimate


class RuleDocument(TimestampedModel):
    """Persisted rule shape."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    name: str = Field(min_length=3, max_length=140)
    description: str = ""
    rule_type: RuleType
    severity: Severity = Severity.MEDIUM
    enabled: bool = True
    mode: RuleMode = RuleMode.SHADOW
    version: int = 1
    created_by: str = "demo-user"
    parameters: RuleParameters
    metrics: RuleMetrics = Field(default_factory=RuleMetrics)


class RuleCreate(BaseModel):
    name: str = Field(min_length=3, max_length=140)
    description: str = ""
    severity: Severity = Severity.MEDIUM
    mode: RuleMode = RuleMode.SHADOW
    enabled: bool = True
    parameters: RuleParameters


class RuleUpdate(BaseModel):
    """Partial update — only allowed fields."""
    description: str | None = None
    severity: Severity | None = None
    enabled: bool | None = None
    mode: RuleMode | None = None
    parameters: RuleParameters | None = None


class RuleTestRequest(BaseModel):
    """Test a rule (or rule definition) against the last N transactions."""
    parameters: RuleParameters
    sample_size: int = Field(default=1000, ge=10, le=10_000)


class RuleTestResult(BaseModel):
    sample_size: int
    would_quarantine: int
    sample_hits: list[dict] = Field(default_factory=list)
    elapsed_ms: int


# =========================================================================
#       PR-1 / PR-6 — Rich rule lifecycle, ownership, metrics_trend
# =========================================================================
# These extend (not replace) `RuleDocument` above. The rule_repo (PR-6)
# appends to `version_history` on update and a daily worker recomputes
# `metrics_trend_30d`.

class RuleVersionHistoryEntry(BaseModel):
    """One immutable diff in a rule's edit history.

    `diff` is a flat dict keyed by JSON-pointer-like paths whose values are
    `[old_value, new_value]` pairs."""
    version: int = Field(ge=1)
    changed_at: datetime
    changed_by: str
    diff: dict = Field(default_factory=dict)


class RuleOwnership(BaseModel):
    business_owner: str
    compliance_reviewer: str | None = None
    regulatory_reference: str | None = None
    runbook_url: str | None = None


class RuleMetricsTrendPoint(BaseModel):
    date: str  # YYYY-MM-DD
    hit_count: int = 0
    fp_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    revenue_protected_myr: float = 0.0


class RuleAbTest(BaseModel):
    """When a rule is being shadow-vs-enforce A/B-tested, this block holds
    the active comparison. Null when no test running."""
    started_at: datetime
    arm_a_mode: RuleMode
    arm_b_mode: RuleMode
    arm_a_traffic_pct: float = Field(default=0.5, ge=0.0, le=1.0)
    arm_a_metrics: dict = Field(default_factory=dict)
    arm_b_metrics: dict = Field(default_factory=dict)
    decision: Literal["pending", "promote_a", "promote_b", "abandoned"] = "pending"
    decided_at: datetime | None = None


class RuleMetricsRich(BaseModel):
    """Extended metrics block for PR-6+. Keep `RuleMetrics` (above) for the
    legacy lean shape; new code paths read this."""
    hit_count_24h: int = 0
    hit_count_7d: int = 0
    hit_count_30d: int = 0
    hit_count_total: int = 0
    last_hit_at: datetime | None = None
    false_positive_rate_30d: float = Field(default=0.0, ge=0.0, le=1.0)
    true_positive_rate_30d: float = Field(default=0.0, ge=0.0, le=1.0)
    median_resolution_time_minutes: float = 0.0
    revenue_protected_30d_myr: float = 0.0
    false_positive_ops_cost_30d_myr: float = 0.0
    net_value_30d_myr: float = 0.0


class RuleHitSample(BaseModel):
    case_id: str
    fired_at: datetime
    disposition: str | None = None


class RuleTestResultEntry(BaseModel):
    tested_at: datetime
    tested_by: str
    sample_size: int
    would_quarantine: int
    elapsed_ms: int


class RuleDocumentV3(TimestampedModel):
    """Persisted rule shape with full lifecycle / governance / metrics.

    Coexists with `RuleDocument` during transition. PR-6 flips writes to
    this shape; legacy reads continue to work because every new field is
    optional or has a sensible default."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    schema_version: int = Field(default=3, alias="_schema_version")
    name: str = Field(min_length=3, max_length=140)
    description: str = ""
    rule_type: RuleType
    severity: Severity = Severity.MEDIUM
    enabled: bool = True
    mode: RuleMode = RuleMode.SHADOW
    version: int = 1

    parameters: RuleParameters

    version_history: list[RuleVersionHistoryEntry] = Field(default_factory=list)
    ownership: RuleOwnership | None = None

    metrics: RuleMetricsRich = Field(default_factory=RuleMetricsRich)
    metrics_trend_30d: list[RuleMetricsTrendPoint] = Field(default_factory=list)

    ab_test: RuleAbTest | None = None
    recent_hits_sample: list[RuleHitSample] = Field(default_factory=list)
    test_results: list[RuleTestResultEntry] = Field(default_factory=list)

    created_by: str = "demo-user"
