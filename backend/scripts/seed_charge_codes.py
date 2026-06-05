"""Seed the canonical Acme charge code catalog (PR-5).

Idempotent — re-running upserts every entry by `code`. Counts are
reported per outcome so seed pipelines can sanity-check progress.

Catalog matches the master plan §PR-5 list:
- CC_SUBSCRIPTION_BASE, CC_SUBSCRIPTION_ADDON
- CC_PPV_DIRECT, CC_BUNDLE_PPV
- CC_DEVICE_FEE, CC_LATE_FEE
- CC_TERMINATION_FEE, CC_PROMO_REBATE
- CC_BILLING_ADJUSTMENT, CC_REFUND
- CC_BIZ_SPORTS_PER_OUTLET (commercial-only)

Run via:
    python -m scripts.seed_charge_codes
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.charge_code_repo import ChargeCodeRepository

logger = get_logger(__name__)


_FAR_FUTURE = datetime(2099, 12, 31, tzinfo=timezone.utc)
_EFFECTIVE_FROM = datetime(2024, 1, 1, tzinfo=timezone.utc)
_APPROVED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Standard SST tax — applies to revenue-bearing lines, not refunds /
# discounts / adjustments.
_TAX_SST = {"taxable": True, "tax_code": "SST", "tax_rate": 0.06}
_TAX_NONE = {"taxable": False, "tax_code": None, "tax_rate": 0.0}

_APPROVAL = {
    "created_by": "seed",
    "created_at": _APPROVED_AT,
    "approved_by": "seed",
    "approved_at": _APPROVED_AT,
}

_EFFECTIVE = {"starts_at": _EFFECTIVE_FROM, "ends_at": _FAR_FUTURE}


def _entry(
    *,
    code: str,
    name: str,
    description: str,
    revenue_category: str,
    gl_account: str,
    applies_to: list[str],
    tax: dict,
) -> dict:
    return {
        "_schema_version": 3,
        "code": code,
        "name": name,
        "description": description,
        "revenue_category": revenue_category,
        "gl_account": gl_account,
        "tax": tax,
        "applies_to": applies_to,
        "approval": dict(_APPROVAL),
        "effective_period": dict(_EFFECTIVE),
        "deprecated": False,
        "deprecated_at": None,
        "usage": {"hit_count_30d": 0, "hit_count_total": 0},
        "runbook_url": None,
    }


CANONICAL_CODES: list[dict] = [
    _entry(
        code="CC_SUBSCRIPTION_BASE",
        name="Subscription — base monthly fee",
        description="Recurring monthly subscription charge for residential customers.",
        revenue_category="subscription",
        gl_account="4000-SUB-RESIDENTIAL",
        applies_to=["residential"],
        tax=_TAX_SST,
    ),
    _entry(
        code="CC_SUBSCRIPTION_ADDON",
        name="Subscription — add-on package",
        description="Add-on package layered on top of a base subscription.",
        revenue_category="addon",
        gl_account="4010-SUB-ADDON",
        applies_to=["residential", "commercial"],
        tax=_TAX_SST,
    ),
    _entry(
        code="CC_PPV_DIRECT",
        name="Pay-per-view — direct purchase",
        description="One-off PPV title purchase outside any bundle.",
        revenue_category="ppv",
        gl_account="4100-PPV-DIRECT",
        applies_to=["residential"],
        tax=_TAX_SST,
    ),
    _entry(
        code="CC_BUNDLE_PPV",
        name="Pay-per-view — bundle inclusion",
        description="PPV charge that should be absorbed inside a bundle. "
                    "Surfacing this code alongside CC_PPV_DIRECT in the same "
                    "window indicates a duplicate-charge case (PR-6 rule).",
        revenue_category="ppv",
        gl_account="4101-PPV-BUNDLE",
        applies_to=["residential"],
        tax=_TAX_SST,
    ),
    _entry(
        code="CC_DEVICE_FEE",
        name="Device fee",
        description="One-off STB / hardware fee.",
        revenue_category="device",
        gl_account="4200-DEVICE",
        applies_to=["residential", "commercial"],
        tax=_TAX_SST,
    ),
    _entry(
        code="CC_LATE_FEE",
        name="Late payment fee",
        description="Charged on past-due bill cycles after grace period.",
        revenue_category="fee",
        gl_account="4300-LATE-FEE",
        applies_to=["residential", "commercial"],
        tax=_TAX_NONE,
    ),
    _entry(
        code="CC_TERMINATION_FEE",
        name="Early termination fee",
        description="Levied when a customer terminates within the lock-in period.",
        revenue_category="fee",
        gl_account="4310-ETF",
        applies_to=["residential", "commercial"],
        tax=_TAX_NONE,
    ),
    _entry(
        code="CC_PROMO_REBATE",
        name="Promotional rebate",
        description="Negative-amount line offsetting a base charge under an "
                    "approved promotion.",
        revenue_category="discount",
        gl_account="5000-PROMO-REBATE",
        applies_to=["residential", "commercial"],
        tax=_TAX_NONE,
    ),
    _entry(
        code="CC_BILLING_ADJUSTMENT",
        name="Billing adjustment",
        description="Manual adjustment posted by ops (positive or negative).",
        revenue_category="adjustment",
        gl_account="5100-ADJUSTMENT",
        applies_to=["residential", "commercial"],
        tax=_TAX_NONE,
    ),
    _entry(
        code="CC_REFUND",
        name="Refund",
        description="Refund posted against a previously billed transaction.",
        revenue_category="refund",
        gl_account="5200-REFUND",
        applies_to=["residential", "commercial"],
        tax=_TAX_NONE,
    ),
    _entry(
        code="CC_BIZ_SPORTS_PER_OUTLET",
        name="Business Sports — per outlet fee",
        description="Commercial-only base subscription for the Business Sports "
                    "package, billed per outlet.",
        revenue_category="subscription",
        gl_account="4500-BIZ-SPORTS",
        applies_to=["commercial"],
        tax=_TAX_SST,
    ),
]


async def seed_charge_codes(db) -> dict[str, int]:
    """Upsert every canonical code. Returns `{inserted, updated, total}`."""
    repo = ChargeCodeRepository(db)
    inserted = 0
    updated = 0
    for entry in CANONICAL_CODES:
        outcome, _doc = await repo.upsert(entry)
        if outcome == "inserted":
            inserted += 1
        else:
            updated += 1
    total = inserted + updated
    logger.info(
        "charge_codes_seeded",
        inserted=inserted,
        updated=updated,
        total=total,
    )
    return {"inserted": inserted, "updated": updated, "total": total}


async def _main() -> None:
    configure_logging()
    await connect_mongo()
    try:
        await seed_charge_codes(get_db())
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(_main())
