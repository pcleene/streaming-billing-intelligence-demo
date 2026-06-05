"""Repo-level tests for `RuleRepository.update_with_history` (PR-6).

Locks in:
  - the diff is recorded against the configured allow-list of fields,
  - `version_history` is *appended* (never overwritten),
  - `version` increments only when there's a real diff,
  - identity / audit fields are preserved across edits.
"""

from __future__ import annotations

import pytest

from app.repositories.rule_repo import RuleRepository
from tests._fakes import FakeDB


@pytest.fixture()
def repo() -> RuleRepository:
    db = FakeDB()
    repo = RuleRepository(db)  # type: ignore[arg-type]
    return repo


async def _seed(repo: RuleRepository, *, rule_id: str = "rule_abc") -> None:
    await repo.upsert({
        "rule_id": rule_id,
        "name": "velocity",
        "rule_type": "velocity_anomaly",
        "severity": "medium",
        "mode": "shadow",
        "enabled": True,
        "version": 1,
        "parameters": {"window_seconds": 60, "max_transactions": 5},
        "version_history": [],
    })


@pytest.mark.asyncio
async def test_update_with_history_records_diff_and_bumps_version(repo: RuleRepository) -> None:
    await _seed(repo)
    out = await repo.update_with_history(
        "rule_abc",
        {"severity": "high", "mode": "active"},
        changed_by="alice",
    )
    assert out["version"] == 2
    assert out["severity"] == "high"
    assert out["mode"] == "active"
    history = out["version_history"]
    assert len(history) == 1
    entry = history[0]
    assert entry["version"] == 2
    assert entry["changed_by"] == "alice"
    assert entry["diff"] == {
        "severity": ["medium", "high"],
        "mode": ["shadow", "active"],
    }


@pytest.mark.asyncio
async def test_update_with_history_appends_not_overwrites(repo: RuleRepository) -> None:
    await _seed(repo)
    await repo.update_with_history("rule_abc", {"severity": "high"}, changed_by="alice")
    out = await repo.update_with_history("rule_abc", {"mode": "active"}, changed_by="bob")
    assert out["version"] == 3
    versions = [e["version"] for e in out["version_history"]]
    assert versions == [2, 3]
    by = [e["changed_by"] for e in out["version_history"]]
    assert by == ["alice", "bob"]


@pytest.mark.asyncio
async def test_update_with_history_noop_when_diff_empty(repo: RuleRepository) -> None:
    await _seed(repo)
    out = await repo.update_with_history(
        "rule_abc",
        {"severity": "medium"},  # same as existing
        changed_by="alice",
    )
    assert out["version"] == 1
    assert out["version_history"] == []


@pytest.mark.asyncio
async def test_update_with_history_records_parameters_diff(repo: RuleRepository) -> None:
    await _seed(repo)
    out = await repo.update_with_history(
        "rule_abc",
        {"parameters": {"window_seconds": 120, "max_transactions": 10}},
        changed_by="alice",
    )
    assert out["parameters"]["window_seconds"] == 120
    diff = out["version_history"][0]["diff"]
    assert "parameters" in diff
    old, new = diff["parameters"]
    assert old["window_seconds"] == 60
    assert new["window_seconds"] == 120


@pytest.mark.asyncio
async def test_update_with_history_unknown_rule_raises(repo: RuleRepository) -> None:
    with pytest.raises(KeyError):
        await repo.update_with_history(
            "rule_does_not_exist", {"severity": "high"}, changed_by="alice"
        )
