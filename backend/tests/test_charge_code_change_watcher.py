"""PR-5 — charge_code_change_watcher.apply_change tests.

The async tail loop is the aiokafka-equivalent glue layer; covered by
integration tests when a live Mongo is available. The change-handling
seam is `apply_change(change, *, cache)`. We drive it with synthetic
change-stream events to assert the cache mutates correctly.
"""

from __future__ import annotations

import pytest

from app.services.charge_code_cache import ChargeCodeCache
from app.workers.charge_code_change_watcher import apply_change


def _doc(code: str, *, deprecated: bool = False) -> dict:
    return {
        "_schema_version": 3,
        "code": code,
        "name": f"Test {code}",
        "revenue_category": "subscription",
        "deprecated": deprecated,
    }


@pytest.fixture
def cache() -> ChargeCodeCache:
    c = ChargeCodeCache()
    c._loaded = True  # simulate post-load steady state
    return c


def test_insert_event_adds_to_cache(cache: ChargeCodeCache) -> None:
    change = {"operationType": "insert", "fullDocument": _doc("CC_NEW")}

    action = apply_change(change, cache=cache)

    assert action == "upsert"
    assert cache.validate("CC_NEW") is True


def test_update_event_overlays_cache(cache: ChargeCodeCache) -> None:
    cache.upsert(_doc("CC_X"))
    change = {
        "operationType": "update",
        "fullDocument": _doc("CC_X", deprecated=True),
    }

    action = apply_change(change, cache=cache)

    assert action == "upsert"
    assert cache.validate("CC_X") is False


def test_replace_event_overlays_cache(cache: ChargeCodeCache) -> None:
    cache.upsert(_doc("CC_X"))
    change = {
        "operationType": "replace",
        "fullDocument": _doc("CC_X", deprecated=True),
    }

    action = apply_change(change, cache=cache)

    assert action == "upsert"
    assert cache.validate("CC_X") is False


def test_delete_event_with_pre_image_removes(cache: ChargeCodeCache) -> None:
    cache.upsert(_doc("CC_X"))
    change = {
        "operationType": "delete",
        "fullDocumentBeforeChange": {"code": "CC_X"},
    }

    action = apply_change(change, cache=cache)

    assert action == "remove"
    assert cache.get("CC_X") is None


def test_delete_event_with_document_key_removes(cache: ChargeCodeCache) -> None:
    cache.upsert(_doc("CC_X"))
    change = {
        "operationType": "delete",
        "documentKey": {"code": "CC_X"},
    }

    action = apply_change(change, cache=cache)

    assert action == "remove"
    assert cache.get("CC_X") is None


def test_insert_without_code_is_noop(cache: ChargeCodeCache) -> None:
    change = {"operationType": "insert", "fullDocument": {"name": "no code"}}

    action = apply_change(change, cache=cache)

    assert action == "noop"


def test_unknown_operation_is_noop(cache: ChargeCodeCache) -> None:
    change = {"operationType": "invalidate", "fullDocument": _doc("CC_X")}

    action = apply_change(change, cache=cache)

    assert action == "noop"
