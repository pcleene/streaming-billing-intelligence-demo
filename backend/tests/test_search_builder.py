"""SearchPipelineBuilder fluent-interface tests."""

from __future__ import annotations

import pytest

from app.pipelines.search_builder import SearchPipelineBuilder


def test_vector_search_must_be_first() -> None:
    b = SearchPipelineBuilder().with_metadata_filter({"x": 1})
    with pytest.raises(ValueError):
        b.with_vector_search([0.1] * 4, "idx")


def test_score_threshold_requires_vector_search() -> None:
    with pytest.raises(ValueError):
        SearchPipelineBuilder().with_score_threshold(0.5)


def test_full_pipeline_assembly() -> None:
    pipe = (
        SearchPipelineBuilder()
        .with_vector_search([0.1] * 4, "idx_x", k=3, num_candidates=60)
        .with_metadata_filter({"disposition": "true_positive"})
        .with_customer_context()
        .with_score_threshold(0.7)
        .with_projection(["case_id", "score"])
        .with_limit(3)
        .build()
    )
    assert "$vectorSearch" in pipe[0]
    assert pipe[0]["$vectorSearch"]["index"] == "idx_x"
    assert pipe[0]["$vectorSearch"]["limit"] == 3
    # Score is projected immediately after vector search
    assert pipe[1]["$set"] == {"score": {"$meta": "vectorSearchScore"}}
    # Customer lookup + unwind present (PR-1: CUSTOMERS aliases
    # `customers_residential` until PR-15).
    from app.core.constants import CUSTOMERS
    assert any("$lookup" in s and s["$lookup"]["from"] == CUSTOMERS for s in pipe)
    assert any("$unwind" in s for s in pipe)
    # Score threshold applied
    assert any(s.get("$match") == {"score": {"$gte": 0.7}} for s in pipe)


def test_empty_metadata_filter_is_skipped() -> None:
    pipe = (
        SearchPipelineBuilder()
        .with_vector_search([0.1] * 4, "idx")
        .with_metadata_filter({})
        .build()
    )
    # No $match for an empty filter
    assert sum(1 for s in pipe if "$match" in s) == 0


def test_build_empty_raises() -> None:
    with pytest.raises(ValueError):
        SearchPipelineBuilder().build()
