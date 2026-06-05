"""Customer schemas — unified consolidated view.

Modeling decision (ADR-001 / schema-design): embed cohesive data accessed
together (profile + active subscriptions + active promotions + entitlements +
recent_support). Reference unbounded sets (full transaction history → its own
collection). Capped recent_transactions array (≤50) embedded for the Customer
360 "timeline preview" hot path. `open_cases` and `latest_features` are also
embedded so the 360 read is a single document fetch — no $lookup fan-out
(see ADR-011).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .common import CaseStatus, GeoJSONPoint, Severity, TimestampedModel


class CustomerSegment(str, Enum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"


class Address(BaseModel):
    line1: str
    line2: str | None = None
    city: str
    state: str
    postcode: str
    country: str = "MY"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class Subscription(BaseModel):
    package_code: str
    package_name: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    monthly_fee_myr: float
    started_at: datetime
    next_billing_at: datetime | None = None


class Promotion(BaseModel):
    promotion_code: str
    description: str
    discount_pct: float | None = None
    discount_amount_myr: float | None = None
    valid_from: datetime
    valid_to: datetime


class Entitlement(BaseModel):
    """Entitlement = right to consume content (e.g. PPV / channel)."""
    content_id: str
    content_name: str
    granted_at: datetime
    expires_at: datetime | None = None


class RecentTransactionEmbed(BaseModel):
    """Capped (≤50) recent transaction summary embedded for timeline preview.

    V3 names (`total_myr`, `total_discount_myr`) match the persisted
    transaction document so the embed is a 1:1 projection — no rename
    bookkeeping in the read path.
    """
    transaction_id: str
    timestamp: datetime
    transaction_type: str
    total_myr: float
    total_discount_myr: float = 0.0
    merchant_id: str
    quarantined: bool = False


class OpenCaseEmbed(BaseModel):
    """Open / under-review quarantine cases embedded on the customer.

    Bounded by analyst SLA — a customer with hundreds of open cases is itself
    an alarm. `ai_assist` payloads stay on the parent `quarantine_cases`
    document; the embed is a thin pointer for the 360 view.
    """
    case_id: str
    transaction_id: str | None = None
    severity: Severity
    status: CaseStatus  # open | under_review only
    rule_types: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LatestFeaturesEmbed(BaseModel):
    """Snapshot of the freshest feature row for the customer.

    The full features doc remains in its own collection (Spark / online
    feature pipeline owns it); this is a read-optimised mirror so the 360
    view doesn't need a $lookup.
    """
    as_of: datetime
    txn_count_1h: int = 0
    txn_count_24h: int = 0
    txn_count_7d: int = 0
    spend_24h_myr: float = 0.0
    spend_7d_myr: float = 0.0
    discount_rate_30d: float = 0.0
    quarantine_count_30d: int = 0
    minutes_since_last_txn: float | None = None
    minutes_since_last_quarantine: float | None = None
    package_value_myr: float = 0.0
    spend_to_package_ratio: float = 0.0


class SupportEvent(BaseModel):
    ticket_id: str
    summary: str
    opened_at: datetime
    closed_at: datetime | None = None
    sentiment: str | None = None  # positive/neutral/negative


# --- Phase B.6 — next-best-offer projection ------------------------------

ChurnRiskBand = Literal["low", "medium", "high"]
NboOfferType = Literal[
    "upgrade", "addon", "retention_discount", "winback", "loyalty_perk"
]


class ChurnRisk(BaseModel):
    """Heuristic churn risk derived from embedded fields only — no joins."""
    band: ChurnRiskBand = "low"
    score: float = Field(0.0, ge=0.0, le=1.0)
    drivers: list[str] = Field(default_factory=list)


class NextBestOffer(BaseModel):
    """One offer recommendation for the customer."""
    offer_id: str
    offer_type: NboOfferType
    title: str
    rationale: str
    expected_uplift_myr: float = 0.0
    priority: int = Field(default=1, ge=1, le=10)


class CustomerRecommendations(BaseModel):
    """NBO + churn projection. Persisted on the customer doc.

    Computed from embedded fields only (latest_features, subscriptions,
    open_cases, recent_transactions) so this stays a true single-document
    read. Re-computation is on-demand via the route.
    """
    model_config = ConfigDict(populate_by_name=True)

    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    churn_risk: ChurnRisk = Field(default_factory=ChurnRisk)
    next_best_offers: list[NextBestOffer] = Field(default_factory=list)


class CustomerDocument(TimestampedModel):
    """Persisted document shape."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    customer_id: str
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    ic_number: str  # synthetic Malaysian IC
    account_id: str
    segment: CustomerSegment = CustomerSegment.SILVER
    address: Address

    subscriptions: list[Subscription] = Field(default_factory=list)
    active_promotions: list[Promotion] = Field(default_factory=list)
    entitlements: list[Entitlement] = Field(default_factory=list)
    recent_transactions: list[RecentTransactionEmbed] = Field(default_factory=list)
    open_cases: list[OpenCaseEmbed] = Field(default_factory=list)
    latest_features: LatestFeaturesEmbed | None = None
    recent_support: list[SupportEvent] = Field(default_factory=list)

    # Derived / cached for the dashboard
    total_monthly_value_myr: float = 0.0
    lifetime_quarantine_count: int = 0

    # Phase B.6 — NBO projection (computed on demand from embedded fields)
    recommendations: CustomerRecommendations | None = None


# --- API-facing models -------------------------------------------------
class CustomerSummary(BaseModel):
    """Lightweight projection for search results."""
    customer_id: str
    name: str
    ic_number: str
    account_id: str
    tier: "CustomerTier"
    state: str
    total_monthly_value_myr: float


class CustomerSearchResult(BaseModel):
    customer: CustomerSummary
    score: float | None = None  # Atlas Search score


class CrmLagInfo(BaseModel):
    """Returned alongside Customer 360 when CRM lag is simulated."""
    enabled: bool
    lag_hours: int
    last_refresh: datetime
    promotions_potentially_missing: int = 0


# =========================================================================
#                  PR-1 / PR-9 — Rich RetailGroup-tier types
# =========================================================================
# These extend (not replace) the lean shape above. Until PR-15 cleanup, the
# legacy `CustomerDocument` continues to work; new code paths gated by
# `FeatureFlags.RICH_CUSTOMER_360` use `ResidentialCustomerDocument` /
# `CommercialCustomerDocument`. Both are designed to coexist in the same
# collection during transition (validators run at validationLevel=moderate).

CustomerType = Literal["residential", "commercial"]
CustomerTier = Literal["bronze", "silver", "gold", "platinum", "RetailGroup"]
LtvBand = Literal["low", "medium", "high", "very_high"]
ChurnRiskBandV3 = Literal["low", "medium", "high", "very_high"]
EquipmentTypeStr = Literal[
    "set_top_box", "smartcard", "router", "remote_control", "power_adapter",
]
EquipmentStatus = Literal["active", "swapped", "deactivated", "in_repair"]


class AddressV3(BaseModel):
    """Address with GeoJSON Point + service_zone + moved_in_at.

    A superset of the lean `Address` above. Fields are optional so legacy
    shapes still validate under moderate validation. The `location` field
    requires a 2dsphere index on `address.location`.
    """
    line1: str
    line2: str | None = None
    city: str
    state: str
    postcode: str
    country: str = "MY"
    location: GeoJSONPoint | None = None
    service_zone: str | None = None
    moved_in_at: datetime | None = None


class ChannelOptIn(BaseModel):
    channel: Literal[
        "email", "sms", "whatsapp", "push_notification",
        "acme_app_inbox", "account_manager", "telemarketing", "direct_mail",
    ]
    opted_in: bool
    opted_in_date: datetime


class CommunicationPreferences(BaseModel):
    preferred_language: str = "en"
    secondary_language: str | None = None
    quiet_hours_start: str | None = None  # HH:MM
    quiet_hours_end: str | None = None    # HH:MM
    preferred_contact_time: str | None = None
    do_not_disturb: bool = False
    billing_email_format: Literal["html", "plain", "pdf"] = "html"
    marketing_frequency_cap_per_week: int | None = None


class Contact(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    alt_phone: str | None = None
    channel_opt_ins: list[ChannelOptIn] = Field(default_factory=list)
    channel_opt_outs: list[str] = Field(default_factory=list)
    communication_preferences: CommunicationPreferences = Field(
        default_factory=CommunicationPreferences
    )


class UnifiedProfile(BaseModel):
    """Cross-entity identity & contact block.

    Same shape regardless of customer_type. Commercial customers populate
    a parallel `business_profile` (see `CommercialProfile` below).
    """
    name: str
    preferred_name: str | None = None
    ethnicity: str | None = None
    ic_number: str | None = None
    date_of_birth: str | None = None  # ISO 8601 date
    gender: Literal["male", "female", "other"] | None = None
    marital_status: str | None = None
    household_size: int | None = None
    occupation_band: str | None = None

    contact: Contact = Field(default_factory=Contact)
    address: AddressV3 | None = None


class ContractTerms(BaseModel):
    contract_start: datetime
    contract_end: datetime
    auto_renew: bool = True
    early_termination_fee_myr: float = 0.0
    remaining_months: int = 0


class CommercialProfile(BaseModel):
    """Parent / outlet business identity. Lives at `business_profile` on
    `customers_commercial` documents. Outlets inherit most fields by
    reference to `parent_account_id`."""
    business_name: str
    business_registration_no: str
    business_type: Literal["sdn_bhd", "berhad", "sole_proprietor", "partnership", "other"] = "sdn_bhd"
    industry: str | None = None
    venue_capacity: int | None = None
    outlet_label: str | None = None  # outlet docs only
    registered_address: AddressV3 | None = None
    contract_terms: ContractTerms | None = None


# --- Entity profiles (per service line) ----------------------------------

class EntityProfilePayTv(BaseModel):
    member_since: datetime
    primary_package_code: str
    primary_package_name: str
    monthly_fee_myr: float = 0.0
    decoder_count: int | None = None
    viewing_hours_30d: float | None = None
    favorite_genres: list[str] | None = None
    lock_in_until: datetime | None = None
    remaining_lock_in_months: int = 0


class EntityProfileStreaming(BaseModel):
    member_since: datetime
    service: Literal[
        "acme_on_demand", "sooka", "netflix",
        "disney_plus_hotstar", "iqiyi", "viu",
    ]
    monthly_fee_myr: float = 0.0
    watch_time_30d_minutes: float | None = None
    favorite_apps: list[str] | None = None
    concurrent_streams_used_p95: int | None = None


class EntityProfileBusiness(BaseModel):
    """For commercial parents *and* outlets. The `package_inherited_from`
    field is set on outlet docs to the parent customer_id; null on parents."""
    member_since: datetime
    primary_package_code: str | None = None
    primary_package_name: str | None = None
    monthly_fee_myr: float = 0.0
    no_of_outlets: int | None = None
    contract_terms: ContractTerms | None = None
    package_inherited_from: str | None = None  # parent customer_id, outlets only
    viewing_hours_30d_avg_per_screen: float | None = None
    screen_count: int | None = None


# Discriminator-free union; entity profiles are stored under `entity_profiles`
# keyed by entity name. Keeping them as a heterogeneous dict lets future
# services be added without a schema migration.
EntityProfile = (
    EntityProfilePayTv | EntityProfileStreaming | EntityProfileBusiness
)


class MonthlyValuePoint(BaseModel):
    month: str  # YYYY-MM
    value_myr: float | None = None
    value: float | None = None  # for non-currency metrics (hours, counts)


class OutletRevenue(BaseModel):
    outlet_id: str
    outlet_label: str
    revenue_30d_myr: float


class CrossEntityMetrics(BaseModel):
    total_ltv_myr: float = 0.0
    ltv_band: LtvBand = "low"
    cross_sell_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cross_sell_band: Literal["low", "medium", "high"] = "low"
    churn_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    churn_risk_band: ChurnRiskBandV3 = "low"
    engagement_index: float = Field(default=0.0, ge=0.0, le=1.0)

    ltv_trend_12m: list[MonthlyValuePoint] = Field(default_factory=list)
    monthly_spend_trend_12m: list[MonthlyValuePoint] = Field(default_factory=list)
    viewing_hours_trend_12m: list[MonthlyValuePoint] | None = None
    ppv_count_trend_12m: list[MonthlyValuePoint] | None = None
    outlet_revenue_distribution_30d: list[OutletRevenue] | None = None  # commercial


# --- Brand journey & interaction history ---------------------------------

class BrandJourneyEvent(BaseModel):
    entity: str
    event: str
    date: datetime
    details: dict | None = None


class SupportInteraction(BaseModel):
    ticket_id: str
    date: datetime
    channel: str
    agent_id: str
    category: str
    subcategory: str
    sentiment: Literal["positive", "neutral", "negative"] = "neutral"
    resolution: str
    resolution_time_minutes: int = 0
    notes: str | None = None


class MarketingInteraction(BaseModel):
    campaign_id: str
    content_id: str
    channel: str
    sent_at: datetime
    opened_at: datetime | None = None
    clicked_at: datetime | None = None
    converted_at: datetime | None = None
    revenue_attributed_myr: float = 0.0


class ChannelEngagementRates(BaseModel):
    open_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    ctr: float = Field(default=0.0, ge=0.0, le=1.0)
    conversion_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    total_sent: int = 0
    last_engaged_at: datetime | None = None


class InteractionHistory(BaseModel):
    """Bounded interaction streams for the 360 view.

    `support_interactions` and `marketing_interactions` are cap-sliced at
    write time (PR-9 worker maintenance: -20 / -50 respectively).
    """
    support_interactions: list[SupportInteraction] = Field(default_factory=list)
    marketing_interactions: list[MarketingInteraction] = Field(default_factory=list)
    channel_engagement_rates: dict[str, ChannelEngagementRates] = Field(
        default_factory=dict
    )


class ActiveCampaign(BaseModel):
    campaign_id: str
    campaign_name: str
    enrollment_id: str
    enrolled_date: datetime
    enrolled_by: Literal[
        "ml_signal", "account_manager", "system", "self_serve",
    ] = "ml_signal"
    signal_id: str | None = None
    content_asset_id: str | None = None
    content_headline: str | None = None
    recommended_channel: str | None = None
    reasoning: str | None = None
    similar_customer_conversion_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    expected_ltv_uplift_myr: float = 0.0
    similar_customers_sampled: list[str] = Field(default_factory=list)
    status: Literal["scheduled", "in_flight", "converted", "expired"] = "scheduled"
    scheduled_send_at: datetime | None = None
    last_status_change_at: datetime


# --- Equipment ------------------------------------------------------------

class Equipment(BaseModel):
    equipment_id: str
    type: EquipmentTypeStr
    model: str
    serial: str
    smart_card: str | None = None
    status: EquipmentStatus = "active"
    installed_at: datetime
    last_seen_at: datetime | None = None
    firmware_version: str | None = None
    location_in_premises: str | None = None


# Backward-compat shim for code using the enum-style name from the plan
EquipmentType = EquipmentTypeStr  # type: ignore[misc]


# --- Bucketed transaction array (commercial outlets) ---------------------

class TxnBucket(BaseModel):
    """One bucket of denormalised recent transactions for a commercial
    outlet. Bucket pattern (ADR-020): cap at 500 transactions per bucket
    per cycle; alert at 480.
    """
    bucket_id: str
    cycle_id: str
    cycle_start: datetime
    cycle_end: datetime
    transaction_count: int = 0
    ppv_count: int = 0
    total_myr: float = 0.0
    transactions: list[dict] = Field(
        default_factory=list,
        description="Denormalised TransactionSummary previews; max 500 entries.",
    )


# --- Cycle preview --------------------------------------------------------

class CurrentCycle(BaseModel):
    cycle_id: str
    cycle_start: datetime
    cycle_end: datetime
    expected_amount_myr: float = 0.0
    billed_amount_myr: float | None = None
    transactions_to_date: int = 0
    days_remaining: int = 0


# --- NBO + churn (rich) ---------------------------------------------------
# The lean Phase B.6 shapes (`NextBestOffer`, `ChurnRisk`,
# `CustomerRecommendations`) above remain the canonical recommendations
# attached at `customers_*.recommendations`. The rich `CrossEntityMetrics`
# above carries the long-form trend.


# --- Recommendations on customer doc -------------------------------------

class CustomerRecommendationsV3(BaseModel):
    """Rich variant; used when `FeatureFlags.RICH_CUSTOMER_360` is on.

    Persisted at `recommendations` on the customer document (replaces the
    lean `CustomerRecommendations` above when the flag is on)."""
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    next_best_offer: dict | None = None  # see NextBestOfferV3 below
    churn_risk: dict | None = None       # see ChurnRiskV3 below


class NextBestOfferV3(BaseModel):
    offer_id: str
    offer_name: str
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class ChurnRiskV3(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    band: ChurnRiskBandV3 = "low"
    drivers: list[str] = Field(default_factory=list)


# --- Rich customer document (residential / commercial) -------------------

class BaseCustomerDocument(TimestampedModel):
    """Common structure for both residential and commercial customer docs.

    PR-15: `unified_profile` was retired and its scalar fields lifted to root.
    `contact` and `address` remain embedded objects but at root level. Vector
    embedding fields were dropped (no vector search on customer collections —
    only on `quarantine_cases_history`). `segment` was dropped in favor of
    `tier` as the canonical customer classification.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    customer_id: str
    account_id: str
    customer_type: CustomerType
    tier: CustomerTier = "silver"

    # Identity scalars (lifted from unified_profile)
    name: str
    preferred_name: str | None = None
    ic_number: str | None = None
    date_of_birth: str | None = None  # ISO 8601 date
    ethnicity: str | None = None
    gender: Literal["male", "female", "other"] | None = None
    marital_status: str | None = None
    household_size: int | None = None
    occupation_band: str | None = None

    # Embedded identity blocks (root-level)
    contact: Contact = Field(default_factory=Contact)
    address: AddressV3 | None = None

    # `entities` lists which service lines apply (e.g. ["acme_paytv", "acme_streaming"]).
    # `entity_profiles` is keyed by entity name with the per-service block.
    entities: list[str] = Field(default_factory=list)
    entity_profiles: dict[str, dict] = Field(default_factory=dict)

    cross_entity_metrics: CrossEntityMetrics = Field(default_factory=CrossEntityMetrics)

    brand_journey: list[BrandJourneyEvent] = Field(default_factory=list)
    interaction_history: InteractionHistory = Field(default_factory=InteractionHistory)
    active_campaigns: list[ActiveCampaign] = Field(default_factory=list)
    recommendations: CustomerRecommendationsV3 | None = None

    equipment: list[Equipment] = Field(default_factory=list)
    current_cycle: CurrentCycle | None = None

    # Hot-path embeds (mirrored from related collections, ADR-011)
    subscriptions: list[Subscription] = Field(default_factory=list)
    active_promotions: list[Promotion] = Field(default_factory=list)
    entitlements: list[Entitlement] = Field(default_factory=list)
    recent_transactions: list[RecentTransactionEmbed] = Field(default_factory=list)
    open_cases: list[OpenCaseEmbed] = Field(default_factory=list)
    latest_features: LatestFeaturesEmbed | None = None

    # Aggregates
    total_monthly_value_myr: float = 0.0
    lifetime_quarantine_count: int = 0


class ResidentialCustomerDocument(BaseCustomerDocument):
    """Residential-typed document. Lives in `customers_residential`."""
    customer_type: Literal["residential"] = "residential"
    parent_account_id: None = None  # residentials never have a parent


class CommercialCustomerDocument(BaseCustomerDocument):
    """Commercial-typed document. Lives in `customers_commercial`.

    Parents have `parent_account_id = None` and `business_profile.outlet_label
    = None`. Outlets inherit from a parent via `parent_account_id` and carry
    their own `outlet_id` plus an `outlet_label`. Bucketed transaction arrays
    (`txn_buckets`) replace `recent_transactions` for high-volume outlets.
    """
    customer_type: Literal["commercial"] = "commercial"
    parent_account_id: str | None = None
    outlet_id: str | None = None
    business_profile: CommercialProfile
    txn_buckets: list[TxnBucket] = Field(default_factory=list)


# --- Customer index entry (lean routing collection, PR-2) ---------------

class CustomerIndexEntry(BaseModel):
    """One row in `customer_index`. Keyed by `customer_id`. Lets routes
    dispatch to the correct typed collection without scanning."""
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    customer_id: str
    customer_type: CustomerType
    account_id: str
    parent_account_id: str | None = None
    outlet_label: str | None = None
    updated_at: datetime
