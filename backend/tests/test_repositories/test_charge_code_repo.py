"""PR-5 — ChargeCodeRepository tests.

Covers the persistence layer that the cache loads from and the seed
script writes through.

- `get` returns None on miss.
- `upsert` inserts a new doc and reports `"inserted"`.
- `upsert` on an existing code reports `"updated"` and applies the
  patch (idempotent for the seed re-run case).
- `list_all` returns every code ordered by `code`.
- `mark_deprecated` flips `deprecated` true.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.constants import CHARGE_CODES
from app.repositories.charge_code_repo import ChargeCodeRepository
from tests._fakes import FakeDB

_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _doc(code: str = "CC_TEST", **overrides) -> dict:
    base = {
        "_schema_version": 3,
        "code": code,
        "name": f"Test {code}",
        "description": "",
        "revenue_category": "subscription",
        "gl_account": "4000-TEST",
        "tax": {"taxable": True, "tax_code": "SST", "tax_rate": 0.06},
        "applies_to": ["residential"],
        "approval": {
            "created_by": "seed",
            "created_at": _NOW,
            "approved_by": "seed",
            "approved_at": _NOW,
        },
        "effective_period": {"starts_at": _NOW, "ends_at": None},
        "deprecated": False,
        "deprecated_at": None,
        "usage": {"hit_count_30d": 0, "hit_count_total": 0},
    }
    base.update(overrides)
    return base


async def test_get_returns_none_on_miss() -> None:
    repo = ChargeCodeRepository(FakeDB())  # type: ignore[arg-type]
    assert await repo.get("CC_MISSING") is None


async def test_upsert_inserts_new_code() -> None:
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]

    outcome, stored = await repo.upsert(_doc("CC_NEW"))

    assert outcome == "inserted"
    assert stored["code"] == "CC_NEW"
    assert stored["_updated_at"] is not None
    docs = db[CHARGE_CODES]._docs  # type: ignore[attr-defined]
    assert len(docs) == 1


async def test_upsert_updates_existing_code() -> None:
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]
    await repo.upsert(_doc("CC_X", name="First"))

    outcome, stored = await repo.upsert(_doc("CC_X", name="Second"))

    assert outcome == "updated"
    assert stored["name"] == "Second"
    docs = db[CHARGE_CODES]._docs  # type: ignore[attr-defined]
    assert len(docs) == 1, "upsert must not create a duplicate"


async def test_upsert_requires_code_field() -> None:
    repo = ChargeCodeRepository(FakeDB())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="code"):
        await repo.upsert({"name": "no code"})


async def test_list_all_returns_sorted_codes() -> None:
    repo = ChargeCodeRepository(FakeDB())  # type: ignore[arg-type]
    await repo.upsert(_doc("CC_B"))
    await repo.upsert(_doc("CC_A"))
    await repo.upsert(_doc("CC_C"))

    rows = await repo.list_all()

    assert [r["code"] for r in rows] == ["CC_A", "CC_B", "CC_C"]


async def test_mark_deprecated_flips_flag() -> None:
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]
    await repo.upsert(_doc("CC_OLD"))

    n = await repo.mark_deprecated("CC_OLD", deprecated_at=_NOW)

    assert n == 1
    stored = await repo.get("CC_OLD")
    assert stored is not None
    assert stored["deprecated"] is True
    assert stored["deprecated_at"] == _NOW
