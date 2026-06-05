"""PR-AG — `MongoAnalyticsTools` + `AnalyticsTools` Protocol tests.

The agent graph (F1) imports the Protocol; production wires the
dataclass. These tests pin both:

- `runtime_checkable` — `isinstance(tools, AnalyticsTools)` works so
  F1's analytics node can guard against bad DI at startup.
- the dataclass forwards to the repo with the right kwargs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.assist_agent.tools import (
    AnalyticsTools,
    MongoAnalyticsTools,
)


def _make_tools() -> MongoAnalyticsTools:
    return MongoAnalyticsTools(
        transaction_repo=AsyncMock(),
        quarantine_case_repo=AsyncMock(),
        feature_drift_repo=AsyncMock(),
    )


async def test_mongo_analytics_tools_satisfies_protocol() -> None:
    tools = _make_tools()
    assert isinstance(tools, AnalyticsTools)


async def test_customer_pattern_30d_delegates_to_repo() -> None:
    tools = _make_tools()
    tools.transaction_repo.customer_pattern = AsyncMock(
        return_value={"customer_id": "c1", "txn_count": 7}
    )

    result = await tools.customer_pattern_30d("c1")

    tools.transaction_repo.customer_pattern.assert_awaited_once_with(
        "c1", days=30
    )
    assert result == {"customer_id": "c1", "txn_count": 7}


async def test_rule_type_frequency_delegates_to_repo() -> None:
    tools = _make_tools()
    tools.quarantine_case_repo.rule_type_frequency = AsyncMock(
        return_value={"rule_type": "rt", "total_cases": 4}
    )

    result = await tools.rule_type_frequency("rt", days=14)

    tools.quarantine_case_repo.rule_type_frequency.assert_awaited_once_with(
        "rt", days=14
    )
    assert result["total_cases"] == 4


async def test_rule_type_frequency_default_days() -> None:
    tools = _make_tools()
    tools.quarantine_case_repo.rule_type_frequency = AsyncMock(
        return_value={"rule_type": "rt"}
    )

    await tools.rule_type_frequency("rt")

    tools.quarantine_case_repo.rule_type_frequency.assert_awaited_once_with(
        "rt", days=7
    )


async def test_drift_snapshot_delegates_to_repo() -> None:
    tools = _make_tools()
    payload = {"as_of": "2026-05-07", "by_feature": {}, "any_drift_detected": False}
    tools.feature_drift_repo.snapshot_for_features = AsyncMock(
        return_value=payload
    )

    result = await tools.drift_snapshot(["feat_a", "feat_b"])

    tools.feature_drift_repo.snapshot_for_features.assert_awaited_once_with(
        ["feat_a", "feat_b"]
    )
    assert result is payload


async def test_protocol_accepts_arbitrary_async_object() -> None:
    """Anything with the three coroutine methods passes runtime check.

    Pins the contract for tests that swap in lightweight stubs without
    instantiating the dataclass.
    """

    class _Stub:
        async def customer_pattern_30d(self, customer_id: str) -> dict:
            return {}

        async def rule_type_frequency(
            self, rule_type: str, days: int = 7
        ) -> dict:
            return {}

        async def drift_snapshot(
            self, feature_names: list[str]
        ) -> dict:
            return {}

    assert isinstance(_Stub(), AnalyticsTools)
