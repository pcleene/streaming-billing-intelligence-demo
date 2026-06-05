"""Service-level unit tests for the rule engine vertical."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.errors import DuplicateRuleName, RuleNotFound, RuleValidationError
from app.schemas.rule import RuleCreate, RuleUpdate, VelocityAnomalyParams
from app.services.rule_service import RuleService


def _stamp(doc: dict) -> dict:
    """Mimic `RuleRepository.upsert` audit stamping: the real repo writes
    `updated_at` via $set and `created_at` via $setOnInsert, then re-reads
    the persisted document. Tests should see both fields on the returned
    object regardless of whether it was an insert or an update.
    """
    now = datetime.now(timezone.utc)
    out = dict(doc)
    out.setdefault("created_at", now)
    out["updated_at"] = now
    return out


def _service(rules_existing: dict | None = None, txn_hits: list | None = None) -> RuleService:
    rule_repo = AsyncMock()
    rule_repo.get_by_id = AsyncMock(return_value=rules_existing)
    rule_repo.get_by_name = AsyncMock(return_value=None)
    rule_repo.upsert = AsyncMock(side_effect=_stamp)
    rule_repo.update_with_history = AsyncMock(
        side_effect=lambda rid, patch, *, changed_by: _stamp({**(rules_existing or {}), **patch})
    )
    rule_repo.list_all = AsyncMock(return_value=[])
    rule_repo.set_mode = AsyncMock(return_value=None)
    rule_repo.ALLOWED_MODES = ("active", "shadow", "disabled")
    txn_repo = AsyncMock()
    txn_repo.aggregate = AsyncMock(return_value=txn_hits or [])
    return RuleService(rule_repo, txn_repo)


@pytest.mark.asyncio
async def test_create_rule_assigns_audit_fields() -> None:
    svc = _service()
    payload = RuleCreate(
        name="test rule",
        rule_type="velocity_anomaly",
        parameters=VelocityAnomalyParams(window_seconds=60, max_transactions=4),
        severity="medium",
        mode="shadow",
        enabled=True,
    )
    out = await svc.create(payload)
    assert "created_at" in out and "updated_at" in out
    assert out["hit_count"] == 0
    assert out["rule_type"] == "velocity_anomaly"


@pytest.mark.asyncio
async def test_create_rule_rejects_duplicate_name() -> None:
    svc = _service()
    svc._rules.get_by_name = AsyncMock(return_value={"rule_id": "rule_x"})
    payload = RuleCreate(
        name="dup",
        rule_type="velocity_anomaly",
        parameters=VelocityAnomalyParams(window_seconds=60, max_transactions=4),
        severity="medium",
        mode="shadow",
        enabled=True,
    )
    with pytest.raises(DuplicateRuleName):
        await svc.create(payload)


@pytest.mark.asyncio
async def test_get_missing_raises_not_found() -> None:
    svc = _service(rules_existing=None)
    with pytest.raises(RuleNotFound):
        await svc.get("rule_missing")


@pytest.mark.asyncio
async def test_set_mode_validates_value() -> None:
    svc = _service(rules_existing={"rule_id": "r1", "mode": "shadow"})
    with pytest.raises(RuleValidationError):
        await svc.set_mode("r1", mode="garbage")


@pytest.mark.asyncio
async def test_update_routes_through_history() -> None:
    """`RuleService.update` must record history via `update_with_history`,
    not via `upsert` (which would overwrite the doc and lose version_history).
    """
    existing = {
        "rule_id": "r1",
        "name": "x",
        "rule_type": "velocity_anomaly",
        "mode": "shadow",
        "severity": "medium",
        "enabled": True,
        "version": 1,
    }
    svc = _service(rules_existing=existing)
    out = await svc.update("r1", RuleUpdate(severity="high"), changed_by="alice")
    svc._rules.update_with_history.assert_awaited_once()
    args, kwargs = svc._rules.update_with_history.call_args
    assert args[0] == "r1"
    assert args[1] == {"severity": "high"}
    assert kwargs["changed_by"] == "alice"
    assert out["severity"] == "high"


@pytest.mark.asyncio
async def test_test_against_history_invalid_rule_type() -> None:
    svc = _service()
    with pytest.raises(RuleValidationError):
        await svc.test_against_history(rule_type="not_a_rule", parameters={})


@pytest.mark.asyncio
async def test_test_against_history_returns_hit_summary() -> None:
    hits = [{"transaction_id": f"txn_{i}"} for i in range(7)]
    svc = _service(txn_hits=hits)
    out = await svc.test_against_history(
        rule_type="discount_mismatch",
        parameters={"min_discount_amount_myr": 1.0},
        sample_size=100,
    )
    assert out["rule_type"] == "discount_mismatch"
    assert out["sample_size"] == 100
    assert out["hit_count"] == 7
    assert 0 < out["hit_rate"] < 1
    assert len(out["hits"]) == 7
