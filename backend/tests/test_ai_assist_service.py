"""Unit tests for the standardized ai_assist service (Phase B.4).

Mocks the case repo and rag service. Verifies:
  - missing case → CaseNotFound
  - fresh existing assist short-circuits (cached: True)
  - stale assist regenerates and persists
  - degraded RAG path still produces a valid AiAssist
  - bad likelihood is coerced to 'needs_more_info'
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.errors import CaseNotFound
from app.services.ai_assist_service import AiAssistService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _case(*, ai_assist=None) -> dict:
    return {
        "case_id": "case-1",
        "customer_id": "cust_001",
        "status": "open",
        "severity": "medium",
        "rules_triggered": [{"rule_id": "r1", "rule_type": "discount_mismatch",
                              "rule_name": "Discount mismatch", "severity": "medium"}],
        "ai_assist": ai_assist,
    }


def _rag_payload(**overrides) -> dict:
    base = {
        "case_id": "case-1",
        "similar_cases": [
            {"case_id": "case-h-1", "score": 0.82, "disposition": "true_positive"},
            {"case_id": "case-h-2", "score": 0.71, "disposition": "false_positive"},
        ],
        "assist": {
            "summary": "Promotion expired before discount applied.",
            "likelihood": "true_positive",
            "confidence": 0.78,
            "rationale": ["Active promo expired 3 days ago."],
            "recommended_steps": ["Refund discount", "Notify CRM"],
            "references": [
                {"case_id": "case-h-1", "disposition": "true_positive",
                 "score": 0.82, "why_relevant": "Same expired-promo pattern."},
            ],
        },
        "degraded": False,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_missing_case_raises() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = None
    rag = AsyncMock()
    svc = AiAssistService(case_repo, rag)
    with pytest.raises(CaseNotFound):
        await svc.generate("case-missing")


@pytest.mark.asyncio
async def test_fresh_assist_returns_cached() -> None:
    fresh = {
        "summary": "cached", "likelihood": "true_positive", "confidence": 0.9,
        "rationale": ["x"], "recommended_steps": ["y"], "references": [],
        "generated_at": _utcnow().isoformat(),
    }
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case(ai_assist=fresh)
    rag = AsyncMock()
    svc = AiAssistService(case_repo, rag, freshness_seconds=300)
    out = await svc.generate("case-1")
    assert out["cached"] is True
    rag.assist.assert_not_called()
    case_repo.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_stale_assist_regenerates() -> None:
    stale = {
        "summary": "old", "likelihood": "true_positive", "confidence": 0.5,
        "rationale": ["x"], "recommended_steps": ["y"], "references": [],
        "generated_at": (_utcnow() - timedelta(hours=2)).isoformat(),
    }
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case(ai_assist=stale)
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist.return_value = _rag_payload()
    svc = AiAssistService(case_repo, rag, freshness_seconds=300)
    out = await svc.generate("case-1")
    assert out["cached"] is False
    rag.assist.assert_awaited_once()
    case_repo.update_one.assert_awaited_once()
    persisted = case_repo.update_one.call_args.args[1]["$set"]["ai_assist"]
    assert persisted["likelihood"] == "true_positive"
    assert persisted["retrieval"]["k"] == 5
    assert persisted["retrieval"]["retrieved_case_ids"] == ["case-h-1", "case-h-2"]


@pytest.mark.asyncio
async def test_force_bypasses_cache() -> None:
    fresh = {
        "summary": "cached", "likelihood": "true_positive", "confidence": 0.9,
        "rationale": ["x"], "recommended_steps": ["y"], "references": [],
        "generated_at": _utcnow().isoformat(),
    }
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case(ai_assist=fresh)
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist.return_value = _rag_payload()
    svc = AiAssistService(case_repo, rag, freshness_seconds=300)
    out = await svc.generate("case-1", force=True)
    assert out["cached"] is False
    rag.assist.assert_awaited_once()


# ---------------------------------------------------------------------------
# PR-8 — denormalisation + RAG-feedback tests
# ---------------------------------------------------------------------------


def _rag_payload_with_top3() -> dict:
    """A RagService.assist payload that has 4 similar cases — enough to
    exercise the top-3 truncation in `similar_cases_preview`."""
    return {
        "case_id": "case-1",
        "similar_cases": [
            {
                "case_id": "case-h-1",
                "score": 0.91,
                "disposition": "true_positive_refund",
                "analyst_notes": "Same expired-promo pattern.",
                "embedding_text": "Resolved case: discount_mismatch — refund issued.",
                "resolved_at": "2026-01-10T00:00:00+00:00",
            },
            {
                "case_id": "case-h-2",
                "score": 0.83,
                "disposition": "false_positive",
                "analyst_notes": "Valid promo extension.",
                "embedding_text": "Resolved case: valid promo, closed.",
                "resolved_at": "2026-01-12T00:00:00+00:00",
            },
            {
                "case_id": "case-h-3",
                "score": 0.77,
                "disposition": "true_positive_recharge",
                "analyst_notes": "Charge code drift.",
                "embedding_text": "Resolved case: charge code drift.",
                "resolved_at": "2026-02-01T00:00:00+00:00",
            },
            {
                "case_id": "case-h-4",
                "score": 0.71,
                "disposition": "duplicate",
                "analyst_notes": "Duplicate of case-h-1.",
                "embedding_text": "Resolved case: duplicate.",
                "resolved_at": "2026-02-05T00:00:00+00:00",
            },
        ],
        "assist": {
            "summary": "Promotion expired before discount applied.",
            "likelihood": "true_positive",
            "confidence": 0.78,
            "rationale": ["Active promo expired 3 days ago."],
            "recommended_steps": ["Refund discount"],
            "references": [
                {"case_id": "case-h-1", "disposition": "true_positive_refund",
                 "score": 0.91, "why_relevant": "Same expired-promo pattern."},
            ],
        },
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_generate_denormalises_top3_similar_cases_preview() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist.return_value = _rag_payload_with_top3()
    history = AsyncMock()
    history.record_rag_usage.return_value = 1

    svc = AiAssistService(case_repo, rag, history_repo=history)
    out = await svc.generate("case-1")
    assert out["cached"] is False

    persisted_set = case_repo.update_one.call_args.args[1]["$set"]
    previews = persisted_set["similar_cases_preview"]
    # Capped at 3 even though RAG returned 4.
    assert len(previews) == 3
    assert [p["case_id"] for p in previews] == ["case-h-1", "case-h-2", "case-h-3"]
    # `score` → `relevance` mapping.
    assert previews[0]["relevance"] == 0.91
    # Disposition + summary survive.
    assert previews[0]["disposition"] == "true_positive_refund"
    assert "expired-promo" in previews[0]["summary"] or "discount_mismatch" in previews[0]["summary"]
    # `resolved_at` serialised to an ISO string for storage.
    assert isinstance(previews[0]["resolved_at"], str)
    assert previews[0]["resolved_at"].startswith("2026-01-10")


@pytest.mark.asyncio
async def test_generate_increments_used_in_rag_count_per_history_doc() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist.return_value = _rag_payload_with_top3()
    history = AsyncMock()
    history.record_rag_usage.return_value = 1

    svc = AiAssistService(case_repo, rag, history_repo=history)
    await svc.generate("case-1")

    # Called once per retrieved similar case (all 4, not just the
    # previewed top 3 — corpus telemetry tracks what the LLM saw).
    assert history.record_rag_usage.await_count == 4
    seen_ids = sorted(
        call.args[0] for call in history.record_rag_usage.await_args_list
    )
    assert seen_ids == ["case-h-1", "case-h-2", "case-h-3", "case-h-4"]
    # All generation-time calls flag was_useful=False.
    for call in history.record_rag_usage.await_args_list:
        assert call.kwargs["was_useful"] is False


@pytest.mark.asyncio
async def test_register_disposition_feedback_positive_increments_each_preview() -> None:
    previews = [
        {"case_id": "case-h-1", "relevance": 0.91, "disposition": "tp",
         "resolved_at": "2026-01-10T00:00:00+00:00", "summary": "..."},
        {"case_id": "case-h-2", "relevance": 0.83, "disposition": "fp",
         "resolved_at": "2026-01-12T00:00:00+00:00", "summary": "..."},
        {"case_id": "case-h-3", "relevance": 0.77, "disposition": "tp",
         "resolved_at": "2026-02-01T00:00:00+00:00", "summary": "..."},
    ]
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        "similar_cases_preview": previews,
    }
    rag = AsyncMock()
    history = AsyncMock()
    history.record_rag_usage.return_value = 1

    svc = AiAssistService(case_repo, rag, history_repo=history)
    await svc.register_disposition_feedback(case_id="case-1", was_useful=True)

    assert history.record_rag_usage.await_count == 3
    for call in history.record_rag_usage.await_args_list:
        assert call.kwargs["was_useful"] is True
    seen_ids = sorted(
        call.args[0] for call in history.record_rag_usage.await_args_list
    )
    assert seen_ids == ["case-h-1", "case-h-2", "case-h-3"]


@pytest.mark.asyncio
async def test_register_disposition_feedback_negative_increments_each_preview() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        "similar_cases_preview": [
            {"case_id": "case-h-1", "relevance": 0.5, "disposition": "?",
             "resolved_at": "2026-01-10T00:00:00+00:00", "summary": "..."},
            {"case_id": "case-h-2", "relevance": 0.5, "disposition": "?",
             "resolved_at": "2026-01-12T00:00:00+00:00", "summary": "..."},
        ],
    }
    rag = AsyncMock()
    history = AsyncMock()
    history.record_rag_usage.return_value = 1

    svc = AiAssistService(case_repo, rag, history_repo=history)
    await svc.register_disposition_feedback(case_id="case-1", was_useful=False)

    assert history.record_rag_usage.await_count == 2
    for call in history.record_rag_usage.await_args_list:
        assert call.kwargs["was_useful"] is False


@pytest.mark.asyncio
async def test_register_disposition_feedback_no_preview_is_noop() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = {
        "case_id": "case-1",
        # similar_cases_preview missing entirely.
    }
    rag = AsyncMock()
    history = AsyncMock()

    svc = AiAssistService(case_repo, rag, history_repo=history)
    await svc.register_disposition_feedback(case_id="case-1", was_useful=True)
    history.record_rag_usage.assert_not_called()


@pytest.mark.asyncio
async def test_degraded_payload_still_validates() -> None:
    case_repo = AsyncMock()
    case_repo.get_by_id.return_value = _case()
    case_repo.update_one.return_value = 1
    rag = AsyncMock()
    rag.assist.return_value = {
        "case_id": "case-1",
        "similar_cases": [],
        "assist": {
            "summary": "AI assist unavailable: bedrock_failed.",
            "likelihood": "unknown",   # legacy fallback enum value
            "confidence": 0.0,
            "rationale": [],
            "recommended_steps": [],
            "references": [],
        },
        "degraded": True,
        "reason": "bedrock_failed",
    }
    svc = AiAssistService(case_repo, rag, freshness_seconds=300)
    out = await svc.generate("case-1")
    persisted = out["ai_assist"]
    # 'unknown' is coerced to 'needs_more_info'.
    assert persisted["likelihood"] == "needs_more_info"
    # min_length=1 backfilled.
    assert len(persisted["rationale"]) >= 1
    assert len(persisted["recommended_steps"]) >= 1
    assert persisted["degraded"] is True
    assert persisted["degraded_reason"] == "bedrock_failed"
