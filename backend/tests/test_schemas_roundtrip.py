"""PR-1 roundtrip tests for the V3 schemas.

For each schema we build a minimal valid instance, serialize via
`model_dump(by_alias=True)`, re-validate, and assert the round-tripped
dump equals the original. This protects against alias drift,
default-factory regressions, and discriminator breakage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.schemas.bill_cycle import (
    BillCycleDocument,
    CycleAnchors,
)
from app.schemas.charge_code import (
    ChargeCodeApproval,
    ChargeCodeDocument,
    EffectivePeriod,
)
from app.schemas.common import Severity
from app.schemas.crm_snapshot import CrmSnapshotDocument
from app.schemas.customer import (
    AddressV3,
    CommercialCustomerDocument,
    CommercialProfile,
    Contact,
    CustomerIndexEntry,
    ResidentialCustomerDocument,
    UnifiedProfile,
)
from app.schemas.feature import (
    DistributionStats,
    FeatureDriftMetric,
)
from app.schemas.quarantine import (
    QuarantineCaseHistoryV3,
    QuarantineCaseV3,
)
from app.schemas.rule import (
    DiscountMismatchParams,
    RuleDocumentV3,
)
from app.schemas.transaction import (
    BillPeriod,
    CustomerSummary,
    TransactionDocumentV3,
)


_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


# --- Builders ----------------------------------------------------------


def _bill_cycle() -> BillCycleDocument:
    return BillCycleDocument(
        cycle_id="cyc_1",
        customer_id="cust_1",
        customer_type="residential",
        account_id="acc_1",
        cycle_anchors=CycleAnchors(
            cycle_start=_NOW,
            cycle_end=_NOW + timedelta(days=30),
            bill_day_of_month=15,
            cycle_length_days=30,
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _charge_code() -> ChargeCodeDocument:
    return ChargeCodeDocument(
        code="CC_TEST",
        name="Test charge",
        revenue_category="subscription",
        gl_account="4000-001",
        approval=ChargeCodeApproval(
            created_by="alice",
            created_at=_NOW,
            approved_by="bob",
            approved_at=_NOW,
        ),
        effective_period=EffectivePeriod(starts_at=_NOW),
    )


def _crm_snapshot() -> CrmSnapshotDocument:
    return CrmSnapshotDocument(
        snapshot_id="snap_1",
        customer_id="cust_1",
        snapshot_at=_NOW,
        source_system="crm_aurora",
        source_extraction_method="cdc",
        created_at=_NOW,
    )


def _residential_customer() -> ResidentialCustomerDocument:
    return ResidentialCustomerDocument(
        customer_id="cust_R1",
        account_id="acc_R1",
        unified_profile=UnifiedProfile(
            name="Aisha Rahim",
            contact=Contact(),
            address=AddressV3(
                line1="1 Jalan Test",
                city="Kuala Lumpur",
                state="WP",
                postcode="50000",
            ),
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _commercial_customer() -> CommercialCustomerDocument:
    return CommercialCustomerDocument(
        customer_id="cust_C1",
        account_id="acc_C1",
        unified_profile=UnifiedProfile(name="Acme Sports Cafe SDN BHD"),
        business_profile=CommercialProfile(
            business_name="Acme Sports Cafe",
            business_registration_no="202301000123",
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _customer_index() -> CustomerIndexEntry:
    return CustomerIndexEntry(
        customer_id="cust_R1",
        customer_type="residential",
        account_id="acc_R1",
        updated_at=_NOW,
    )


def _transaction_v3() -> TransactionDocumentV3:
    return TransactionDocumentV3(
        transaction_id="txn_1",
        customer_id="cust_R1",
        customer_type="residential",
        account_id="acc_R1",
        cycle_id="cyc_1",
        bill_period=BillPeriod(
            start=_NOW,
            end=_NOW + timedelta(days=30),
            cycle_length_days=30,
            bill_day_of_month=15,
        ),
        timestamp=_NOW,
        transaction_type="subscription_charge",
        channel="auto_debit",
        merchant_id="acme-direct",
        customer_summary=CustomerSummary(name="Aisha Rahim"),
    )


def _quarantine_case_v3() -> QuarantineCaseV3:
    return QuarantineCaseV3(
        case_id="case_1",
        customer_id="cust_R1",
        severity=Severity.HIGH,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _quarantine_case_history_v3() -> QuarantineCaseHistoryV3:
    return QuarantineCaseHistoryV3(
        case_id="case_h_1",
        customer_id="cust_R1",
        disposition="false_positive",
        severity=Severity.MEDIUM,
        analyst_notes="Reviewed; benign promotion mismatch.",
        resolution_summary="Closed as false positive.",
        resolved_at=_NOW,
        embedding_text="case context for embedding",
    )


def _rule_v3() -> RuleDocumentV3:
    return RuleDocumentV3(
        name="Discount mismatch v3",
        rule_type="discount_mismatch",
        parameters=DiscountMismatchParams(),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _feature_drift_metric() -> FeatureDriftMetric:
    stats = DistributionStats(
        n=100, mean=10.0, std=2.5, min=0.0, p25=8.0, p50=10.0, p75=12.0, p99=18.0, max=20.0
    )
    return FeatureDriftMetric(
        feature_name="spend_24h_myr",
        measured_at=_NOW,
        current=stats,
        baseline=stats,
    )


# --- Parametrized roundtrip ------------------------------------------


@pytest.mark.parametrize(
    ("label", "build"),
    [
        ("BillCycleDocument",          _bill_cycle),
        ("ChargeCodeDocument",         _charge_code),
        ("CrmSnapshotDocument",        _crm_snapshot),
        ("ResidentialCustomerDocument", _residential_customer),
        ("CommercialCustomerDocument", _commercial_customer),
        ("CustomerIndexEntry",         _customer_index),
        ("TransactionDocumentV3",      _transaction_v3),
        ("QuarantineCaseV3",           _quarantine_case_v3),
        ("QuarantineCaseHistoryV3",    _quarantine_case_history_v3),
        ("RuleDocumentV3",             _rule_v3),
        ("FeatureDriftMetric",         _feature_drift_metric),
    ],
)
def test_schema_roundtrip(label: str, build) -> None:
    """model_dump(by_alias=True) → model_validate → equal dump."""
    original: BaseModel = build()
    dumped = original.model_dump(by_alias=True, mode="json")
    restored = type(original).model_validate(dumped)
    redumped = restored.model_dump(by_alias=True, mode="json")
    assert dumped == redumped, f"{label}: round-trip drift"


def test_schema_version_alias_used_on_dump() -> None:
    """All V3 docs persist under `_schema_version`, never `schema_version`."""
    samples = [
        _bill_cycle(),
        _charge_code(),
        _crm_snapshot(),
        _residential_customer(),
        _commercial_customer(),
        _customer_index(),
        _transaction_v3(),
        _quarantine_case_v3(),
        _quarantine_case_history_v3(),
        _rule_v3(),
    ]
    for doc in samples:
        d = doc.model_dump(by_alias=True, mode="json")
        assert "_schema_version" in d, f"{type(doc).__name__} missing _schema_version alias"
        assert "schema_version" not in d, f"{type(doc).__name__} leaked schema_version (no alias)"
        assert d["_schema_version"] == 3


def test_residential_customer_rejects_parent_account() -> None:
    """ResidentialCustomerDocument hard-codes parent_account_id = None."""
    with pytest.raises(Exception):
        ResidentialCustomerDocument(
            customer_id="cust_R2",
            account_id="acc_R2",
            unified_profile=UnifiedProfile(name="x"),
            parent_account_id="acc_PARENT",  # not allowed
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_commercial_customer_requires_business_profile() -> None:
    with pytest.raises(Exception):
        CommercialCustomerDocument(
            customer_id="cust_C2",
            account_id="acc_C2",
            unified_profile=UnifiedProfile(name="x"),
            # business_profile missing → required
            created_at=_NOW,
            updated_at=_NOW,
        )
