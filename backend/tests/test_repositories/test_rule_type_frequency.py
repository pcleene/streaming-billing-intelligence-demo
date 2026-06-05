"""PR-AG — QuarantineCaseRepository.rule_type_frequency unit tests.

The agent's analytics node calls this to know whether the cited
rule_type is fresh / week-over-week common, plus the disposition mix
analysts have applied to recent firings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.repositories.quarantine_case_repo import QuarantineCaseRepository
from tests._fakes import FakeDB

_NOW = datetime.now(timezone.utc)


def _case(
    *,
    case_id: str,
    rule_type: str = "termination_fee_anomaly",
    disposition: str | None = None,
    severity: str = "medium",
    status: str = "open",
    created_at: datetime | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "customer_id": f"cust_for_{case_id}",
        "rules_triggered": [{"rule_type": rule_type, "rule_id": f"r_{rule_type}"}],
        "disposition": disposition,
        "severity": severity,
        "status": status,
        "created_at": created_at or _NOW,
    }


async def _seed(db, cases: list[dict]) -> None:
    coll = db["quarantine_cases"]
    for c in cases:
        await coll.insert_one(c)


async def test_rule_type_frequency_total_count() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _case(case_id=f"c{i}", created_at=_NOW - timedelta(days=1))
            for i in range(6)
        ]
        + [
            # Different rule_type — should NOT count.
            _case(case_id="other", rule_type="velocity_anomaly"),
        ],
    )

    result = await repo.rule_type_frequency(
        "termination_fee_anomaly", days=7
    )

    assert result["rule_type"] == "termination_fee_anomaly"
    assert result["window_days"] == 7
    assert result["total_cases"] == 6


async def test_rule_type_frequency_disposition_distribution() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _case(case_id="c1", disposition="false_positive"),
            _case(case_id="c2", disposition="false_positive"),
            _case(case_id="c3", disposition="fraud"),
            _case(case_id="c4", disposition=None, status="open"),
        ],
    )

    result = await repo.rule_type_frequency(
        "termination_fee_anomaly", days=7
    )

    assert result["by_disposition"]["false_positive"] == 2
    assert result["by_disposition"]["fraud"] == 1
    # Cases without disposition fall under the "open" bucket.
    assert result["by_disposition"]["open"] == 1


async def test_rule_type_frequency_severity_distribution() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _case(case_id="s1", severity="low"),
            _case(case_id="s2", severity="medium"),
            _case(case_id="s3", severity="medium"),
            _case(case_id="s4", severity="high"),
            _case(case_id="s5", severity="high"),
            _case(case_id="s6", severity="high"),
        ],
    )

    result = await repo.rule_type_frequency(
        "termination_fee_anomaly", days=7
    )

    assert result["by_severity"]["low"] == 1
    assert result["by_severity"]["medium"] == 2
    assert result["by_severity"]["high"] == 3


async def test_rule_type_frequency_filters_by_window() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _case(case_id="recent_1", created_at=_NOW - timedelta(days=1)),
            _case(case_id="recent_2", created_at=_NOW - timedelta(days=3)),
            _case(case_id="old_1", created_at=_NOW - timedelta(days=30)),
            _case(case_id="old_2", created_at=_NOW - timedelta(days=60)),
        ],
    )

    result = await repo.rule_type_frequency(
        "termination_fee_anomaly", days=7
    )

    assert result["total_cases"] == 2


async def test_rule_type_frequency_open_count() -> None:
    db = FakeDB()
    repo = QuarantineCaseRepository(db)  # type: ignore[arg-type]
    await _seed(
        db,
        [
            _case(case_id="o1", status="open"),
            _case(case_id="o2", status="under_review"),
            _case(case_id="o3", status="resolved"),
            _case(case_id="o4", status="dismissed"),
        ],
    )

    result = await repo.rule_type_frequency(
        "termination_fee_anomaly", days=7
    )

    # `open` and `under_review` count as still-open.
    assert result["open_count"] == 2
