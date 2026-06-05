"""Transaction schemas — the live event shape and the persisted document."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TransactionType(str, Enum):
    SUBSCRIPTION_CHARGE = "subscription_charge"
    PPV_PURCHASE = "ppv_purchase"
    BILLING_ADJUSTMENT = "billing_adjustment"
    DEVICE_FEE = "device_fee"
    LATE_FEE = "late_fee"
    REFUND = "refund"


class TransactionEvent(BaseModel):
    """The shape produced to the MSK topic."""
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    transaction_id: str
    customer_id: str
    timestamp: datetime
    transaction_type: TransactionType
    amount: float = Field(ge=0)
    discount_amount: float = Field(default=0.0, ge=0)
    currency: str = "MYR"
    merchant_id: str
    content_id: str | None = None  # for PPV
    location: dict | None = None   # {state, lat, lng}
    metadata: dict = Field(default_factory=dict)


class TransactionDocument(TransactionEvent):
    """Persisted shape — adds ingestion-side decoration."""
    quarantined: bool = False
    case_ids: list[str] = Field(default_factory=list)
    _ingested_at: datetime | None = None
    _source_topic: str | None = None


# =========================================================================
#       PR-3 / PR-15 — Extended-reference transaction document (V3-only)
# =========================================================================
# This is the only persisted shape since PR-15 retired the
# `EXT_REF_TRANSACTIONS` flag and its legacy fallback. Stream rules read
# directly from this document — never via $lookup.

from typing import Literal  # noqa: E402

from .common import GeoJSONPoint  # noqa: E402


TransactionTypeStr = Literal[
    "subscription_charge", "addon_charge", "ppv_charge",
    "termination_fee", "device_fee", "late_fee", "refund",
    "billing_adjustment", "promo_rebate", "device_purchase",
]


class BillPeriod(BaseModel):
    """Frozen cycle anchors at write time. Avoids a $lookup to bill_cycles
    for hot-path stream consumers."""
    start: datetime
    end: datetime
    cycle_length_days: int
    bill_day_of_month: int


class CustomerSummary(BaseModel):
    """Frozen customer projection at transaction write time.

    Stream processors and rule pipelines read these fields directly. Never
    re-fetch from `customers_*` for a quarantined transaction's evidence —
    the evidence must reflect the state at billing time.
    """
    name: str
    tier: str | None = None
    ic_number: str | None = None
    service_state: str | None = None
    package_at_billing: dict | None = None
    active_promotions_at_billing: list[dict] = Field(default_factory=list)
    active_entitlements_at_billing: list[str] = Field(default_factory=list)
    loyalty_tier_at_billing: str | None = None
    loyalty_member_id: str | None = None

    # Commercial-only fields
    parent_business_name: str | None = None
    outlet_label: str | None = None
    venue_capacity: int | None = None


class Item(BaseModel):
    """One line item on a transaction.

    `charge_code` must resolve against the catalog (PR-5). `metadata` carries
    rule-relevant fields that don't fit the canonical shape (e.g. decoder_id,
    earned/unearned amounts, mid_cycle_change flags).
    """
    item_id: str
    item_type: Literal[
        "subscription_charge", "addon_charge", "ppv_charge",
        "termination_fee", "device_fee", "late_fee", "refund",
        "billing_adjustment", "promo_rebate",
    ]
    product_ref: str | None = None
    name: str
    category: str | None = None
    subcategory: str | None = None
    brand: str | None = None
    billing_period_days: int | None = None
    quantity: float = 1.0
    unit_price_myr: float = 0.0
    discount_per_unit_myr: float = 0.0
    line_total_myr: float = 0.0
    promo_id: str | None = None
    charge_code: str
    loyalty_points_earned: float = 0.0
    tax_inclusive: bool = False

    # Termination fee specific
    computed_expected_fee_myr: float | None = None
    expected_vs_billed_delta_myr: float | None = None

    # Refund specific
    refund_reference_transaction_id: str | None = None
    refund_reason: str | None = None

    # Free-form rule-relevant metadata
    metadata: dict | None = None


class Discount(BaseModel):
    promo_id: str
    description: str
    applies_to_item_ids: list[str] = Field(default_factory=list)
    amount_myr: float = 0.0


class Tax(BaseModel):
    rate: float = 0.0
    name: str = "SST"
    amount_myr: float = 0.0


class Payment(BaseModel):
    method: Literal[
        "credit_card", "bank_transfer", "auto_debit",
        "post_paid_invoice", "fpx", "ewallet", "cash",
    ]
    card_last4: str | None = None
    card_network: Literal["visa", "mastercard", "amex", "unionpay"] | None = None
    auto_debit: bool = False
    installment_months: int | None = None
    auth_code: str | None = None
    settled_at: datetime | None = None
    psp: str | None = None
    psp_reference: str | None = None


class Loyalty(BaseModel):
    points_earned: float = 0.0
    points_redeemed: float = 0.0
    tier_at_purchase: str | None = None
    member_id: str | None = None
    bonus_multiplier: float = 1.0
    points_balance_after: float = 0.0


class CampaignAttribution(BaseModel):
    campaign_id: str
    content_id: str | None = None
    channel: str | None = None
    attributed_revenue_myr: float = 0.0


class ComputedSignals(BaseModel):
    """Pre-computed signals for the feature store and rule pipelines.

    Populated at write time so neither downstream consumer has to re-derive
    from the transaction history. Per ADR-022, these are the only signals
    rule pipelines may consume from the transaction document directly.
    """
    amount_vs_avg_30d_pct: float = 0.0
    discount_pct_of_subtotal: float = 0.0
    is_first_ppv: bool = False
    is_lock_in_period: bool = False
    is_promo_active: bool = False
    geographic_distance_from_home_km: float = 0.0
    txn_velocity_5m: int = 0
    termination_fee_pct_of_expected: float | None = None


class TransactionLocation(BaseModel):
    state: str | None = None
    city: str | None = None
    service_zone: str | None = None
    point: GeoJSONPoint | None = None


class TransactionDocumentV3(BaseModel):
    """Extended-reference persisted shape (PR-3 write path).

    The single source of truth for the `transactions` collection going
    forward. Carries denormalised customer + cycle + item state so rule
    pipelines and the feature engineer never need a $lookup.
    """
    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=3, alias="_schema_version")
    transaction_id: str

    # Identity & routing (denormalised for stream processors)
    customer_id: str
    customer_type: Literal["residential", "commercial"]
    account_id: str
    outlet_id: str | None = None
    parent_account_id: str | None = None
    cycle_id: str
    bill_period: BillPeriod

    timestamp: datetime
    transaction_type: TransactionTypeStr
    channel: str
    entity: str | None = None
    merchant_id: str
    currency: str = "MYR"

    location: TransactionLocation = Field(default_factory=TransactionLocation)

    customer_summary: CustomerSummary

    items: list[Item] = Field(default_factory=list)

    subtotal_myr: float = 0.0
    discounts: list[Discount] = Field(default_factory=list)
    total_discount_myr: float = 0.0
    tax: Tax = Field(default_factory=Tax)
    total_myr: float = 0.0

    payment: Payment | None = None
    loyalty: Loyalty | None = None

    quarantined: bool = False
    quarantine_case_ids: list[str] = Field(default_factory=list)

    campaign_attribution: CampaignAttribution | None = None

    is_returned: bool = False
    is_refund: bool = False

    computed_signals: ComputedSignals = Field(default_factory=ComputedSignals)

    # Stream / source metadata (alias-prefixed in DB; populated by ingest)
    ingested_at: datetime | None = Field(default=None, alias="_ingested_at")
    source_topic: str | None = Field(default=None, alias="_source_topic")
    source_partition: int | None = Field(default=None, alias="_source_partition")
    source_offset: int | None = Field(default=None, alias="_source_offset")
    event_id: str | None = Field(default=None, alias="_event_id")
