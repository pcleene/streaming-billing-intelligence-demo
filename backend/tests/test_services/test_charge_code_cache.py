"""PR-5 — ChargeCodeCache tests.

Covers the in-memory catalog cache:
- `load(repo)` populates the cache from the repo.
- `get` / `validate` round-trip on loaded data.
- Degraded mode: pre-load `validate` returns True so the write path
  doesn't block on cold start.
- Deprecated codes fail `validate` even when present in the cache.
- `upsert` / `remove` apply change-stream events.
"""

from __future__ import annotations

import pytest

from app.repositories.charge_code_repo import ChargeCodeRepository
from app.services.charge_code_cache import ChargeCodeCache, charge_code_cache
from tests._fakes import FakeDB


def _entry(code: str, *, deprecated: bool = False) -> dict:
    return {
        "_schema_version": 3,
        "code": code,
        "name": f"Test {code}",
        "revenue_category": "subscription",
        "gl_account": "4000-TEST",
        "deprecated": deprecated,
    }


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Each test starts with a clean module-level cache."""
    charge_code_cache.clear()
    yield
    charge_code_cache.clear()


async def test_validate_returns_true_when_unloaded() -> None:
    cache = ChargeCodeCache()
    assert cache.is_loaded is False
    assert cache.validate("CC_ANYTHING") is True


async def test_validate_false_when_loaded_and_missing() -> None:
    cache = ChargeCodeCache()
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]
    await repo.upsert(_entry("CC_KNOWN"))

    n = await cache.load(repo)

    assert n == 1
    assert cache.is_loaded is True
    assert cache.validate("CC_KNOWN") is True
    assert cache.validate("CC_MISSING") is False


async def test_validate_false_for_deprecated_code() -> None:
    cache = ChargeCodeCache()
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]
    await repo.upsert(_entry("CC_OLD", deprecated=True))
    await cache.load(repo)

    assert cache.validate("CC_OLD") is False
    assert cache.get("CC_OLD") is not None  # still in the cache


async def test_validate_none_returns_false() -> None:
    cache = ChargeCodeCache()
    assert cache.validate(None) is False


async def test_get_returns_deep_copy() -> None:
    cache = ChargeCodeCache()
    db = FakeDB()
    repo = ChargeCodeRepository(db)  # type: ignore[arg-type]
    await repo.upsert(_entry("CC_X"))
    await cache.load(repo)

    a = cache.get("CC_X")
    a["name"] = "MUTATED"
    b = cache.get("CC_X")

    assert b["name"] != "MUTATED"


async def test_upsert_applies_change_stream_event() -> None:
    cache = ChargeCodeCache()
    cache._loaded = True  # simulate a loaded cache
    cache.upsert(_entry("CC_NEW"))

    assert cache.validate("CC_NEW") is True

    # An update flips deprecated.
    cache.upsert(_entry("CC_NEW", deprecated=True))
    assert cache.validate("CC_NEW") is False


async def test_remove_applies_delete_event() -> None:
    cache = ChargeCodeCache()
    cache._loaded = True
    cache.upsert(_entry("CC_X"))
    assert cache.validate("CC_X") is True

    cache.remove("CC_X")
    assert cache.validate("CC_X") is False


async def test_module_level_singleton_is_isolated_per_test() -> None:
    """The autouse fixture must reset the singleton between tests."""
    assert charge_code_cache.is_loaded is False
    assert charge_code_cache.size == 0
    charge_code_cache._loaded = True
    charge_code_cache.upsert(_entry("CC_TEMP"))
    assert charge_code_cache.size == 1
