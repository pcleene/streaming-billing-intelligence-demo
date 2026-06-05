"""PR-5 — seed_charge_codes idempotency + canonical-set coverage."""

from __future__ import annotations

import pytest

from app.core.constants import CHARGE_CODES
from scripts.seed_charge_codes import CANONICAL_CODES, seed_charge_codes
from tests._fakes import FakeDB


_REQUIRED_CODES = {
    "CC_SUBSCRIPTION_BASE",
    "CC_SUBSCRIPTION_ADDON",
    "CC_PPV_DIRECT",
    "CC_BUNDLE_PPV",
    "CC_DEVICE_FEE",
    "CC_LATE_FEE",
    "CC_TERMINATION_FEE",
    "CC_PROMO_REBATE",
    "CC_BILLING_ADJUSTMENT",
    "CC_REFUND",
    "CC_BIZ_SPORTS_PER_OUTLET",
}


def test_canonical_codes_cover_master_plan() -> None:
    """Spec: master plan §PR-5 lists 11 canonical codes."""
    seeded = {c["code"] for c in CANONICAL_CODES}
    assert seeded == _REQUIRED_CODES


async def test_seed_inserts_every_canonical_code() -> None:
    db = FakeDB()

    stats = await seed_charge_codes(db)

    assert stats["inserted"] == len(CANONICAL_CODES)
    assert stats["updated"] == 0
    assert stats["total"] == len(CANONICAL_CODES)
    docs = db[CHARGE_CODES]._docs  # type: ignore[attr-defined]
    assert {d["code"] for d in docs} == _REQUIRED_CODES


async def test_seed_is_idempotent() -> None:
    db = FakeDB()

    await seed_charge_codes(db)
    second = await seed_charge_codes(db)

    assert second["inserted"] == 0
    assert second["updated"] == len(CANONICAL_CODES)
    docs = db[CHARGE_CODES]._docs  # type: ignore[attr-defined]
    # Idempotent → still exactly one doc per code.
    assert len(docs) == len(CANONICAL_CODES)


async def test_commercial_only_code_carries_correct_applies_to() -> None:
    by_code = {c["code"]: c for c in CANONICAL_CODES}
    assert by_code["CC_BIZ_SPORTS_PER_OUTLET"]["applies_to"] == ["commercial"]
    assert by_code["CC_SUBSCRIPTION_BASE"]["applies_to"] == ["residential"]
