"""PR-5 — BatchCache.validate_charge_code consults the singleton cache.

The pre-PR-5 contract:
- explicit `charge_codes` dict on construction → honoured verbatim.
- no dict → accept-all.

PR-5 narrows option (b): no dict → consult `charge_code_cache`. When
the cache is unloaded (the unit-test default) it stays in degraded
"accept all" mode so existing PR-3 tests keep passing. When the cache
is loaded, the BatchCache mirrors its verdict.
"""

from __future__ import annotations

import pytest

from app.repositories.transaction_repo import BatchCache
from app.services.charge_code_cache import charge_code_cache


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    charge_code_cache.clear()
    yield
    charge_code_cache.clear()


def test_explicit_dict_takes_precedence() -> None:
    cache = BatchCache(charge_codes={"CC_OK": {}})
    # Singleton is unloaded — would otherwise accept all. The explicit
    # dict still rejects unknown codes.
    assert cache.validate_charge_code("CC_OK") is True
    assert cache.validate_charge_code("CC_BAD") is False


def test_singleton_unloaded_accepts_all() -> None:
    cache = BatchCache()
    assert charge_code_cache.is_loaded is False
    assert cache.validate_charge_code("CC_ANYTHING") is True


def test_singleton_loaded_validates_against_catalog() -> None:
    charge_code_cache._loaded = True
    charge_code_cache.upsert({"code": "CC_KNOWN", "deprecated": False})
    cache = BatchCache()

    assert cache.validate_charge_code("CC_KNOWN") is True
    assert cache.validate_charge_code("CC_UNKNOWN") is False


def test_singleton_loaded_rejects_deprecated() -> None:
    charge_code_cache._loaded = True
    charge_code_cache.upsert({"code": "CC_OLD", "deprecated": True})
    cache = BatchCache()

    assert cache.validate_charge_code("CC_OLD") is False


def test_validate_none_is_always_false() -> None:
    cache = BatchCache()
    assert cache.validate_charge_code(None) is False
