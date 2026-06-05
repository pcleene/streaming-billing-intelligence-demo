"""Tests for the deterministic rule-based classifier.

`default_classifier` is the deterministic fallback the graph uses when
the LLM-backed classifier is unavailable. Production wraps it; tests
exercise it directly because every routing decision depends on its
output.
"""

from __future__ import annotations

from app.services.assist_agent.defaults import default_classifier


def _rule(
    rule_type: str = "discount_mismatch", confidence: float | None = None
) -> dict:
    ev: dict = {}
    if confidence is not None:
        ev["confidence"] = confidence
    return {
        "rule_id": "r1",
        "rule_type": rule_type,
        "rule_name": "test rule",
        "severity": "medium",
        "evidence": ev,
    }


def test_high_severity_two_rules_routes_deep() -> None:
    case = {
        "case_id": "c1",
        "severity": "high",
        "rules_triggered": [_rule(), _rule(rule_type="overcharge")],
    }
    out = default_classifier(case)
    assert out["complexity"] == "high"
    assert out["recommended_path"] == "deep"


def test_medium_severity_one_rule_routes_deep_medium_complexity() -> None:
    case = {
        "case_id": "c2",
        "severity": "medium",
        "rules_triggered": [_rule()],
    }
    out = default_classifier(case)
    assert out["complexity"] == "medium"
    assert out["recommended_path"] == "deep"


def test_low_severity_no_rules_routes_fast() -> None:
    case = {
        "case_id": "c3",
        "severity": "low",
        "rules_triggered": [],
    }
    out = default_classifier(case)
    assert out["complexity"] == "low"
    assert out["recommended_path"] == "fast"
    assert out["evidence_strength"] == "weak"


def test_strong_evidence_when_any_rule_confidence_above_threshold() -> None:
    case = {
        "case_id": "c4",
        "severity": "medium",
        "rules_triggered": [
            _rule(confidence=0.4),
            _rule(rule_type="overcharge", confidence=0.85),
        ],
    }
    out = default_classifier(case)
    assert out["evidence_strength"] == "strong"
