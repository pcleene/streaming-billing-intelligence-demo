"""Analytics + retrieval tools the agent graph can call from its nodes.

Each tool is a thin async wrapper around a repository / service method.
The wrappers exist for two reasons:

1.  They give every callable a `name` + `description` so the per-node
    trace entries can name the dependency they invoked.
2.  They translate exceptions into `{"error": "..."}` dicts. Nodes
    treat a tool failure as a *partial-degradation* signal rather than
    a fatal one — the synthesizer can still produce an `AiAssist` from
    whatever survived.

F3 is landing the underlying repo methods (`customer_pattern_30d`,
`rule_type_frequency`, `snapshot_for_features`) in parallel; we DI the
repos so tests inject `AsyncMock` doubles regardless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------
# F3 — Protocol + concrete repo-backed implementation.
#
# The class-based `_BaseTool` subclasses below are the agent-node-facing
# adapters (one tool per call-site, with consistent `name` + error
# wrapping). The Protocol here is the *flat* analytics surface F1's
# nodes import for typing; production wires `MongoAnalyticsTools` and
# tests inject any object that satisfies the three coroutine methods.
# ---------------------------------------------------------------------


@runtime_checkable
class AnalyticsTools(Protocol):
    """Three-method analytics surface consumed by the agent graph."""

    async def customer_pattern_30d(self, customer_id: str) -> dict: ...

    async def rule_type_frequency(
        self, rule_type: str, days: int = 7
    ) -> dict: ...

    async def drift_snapshot(
        self, feature_names: list[str]
    ) -> dict: ...


@dataclass(slots=True)
class MongoAnalyticsTools:
    """Concrete `AnalyticsTools` backed by the V3 repositories.

    Each method is a one-line delegation so the protocol stays cheap
    to satisfy in tests (any AsyncMock-shaped object will do) while
    production gets the real Mongo aggregations underneath.
    """

    transaction_repo: Any
    quarantine_case_repo: Any
    feature_drift_repo: Any

    async def customer_pattern_30d(self, customer_id: str) -> dict:
        """30-day txn pattern: count, mean, stddev, top 3 charge codes."""
        return await self.transaction_repo.customer_pattern(
            customer_id, days=30
        )

    async def rule_type_frequency(
        self, rule_type: str, days: int = 7
    ) -> dict:
        """Window-bound rule_type frequency + disposition / severity mix."""
        return await self.quarantine_case_repo.rule_type_frequency(
            rule_type, days=days
        )

    async def drift_snapshot(self, feature_names: list[str]) -> dict:
        """Latest drift severity for each named feature."""
        return await self.feature_drift_repo.snapshot_for_features(
            feature_names
        )


def build_analytics_tools(db: Any) -> MongoAnalyticsTools:
    """Wire the three repositories needed by the agent's analytics node.

    Imports happen at call time so test modules can monkeypatch
    individual repos without dragging the import graph in at module
    load.
    """
    from app.repositories.feature_drift_metrics_repo import (
        FeatureDriftMetricsRepository,
    )
    from app.repositories.quarantine_case_repo import (
        QuarantineCaseRepository,
    )
    from app.repositories.transaction_repo import TransactionRepository
    return MongoAnalyticsTools(
        transaction_repo=TransactionRepository(db),
        quarantine_case_repo=QuarantineCaseRepository(db),
        feature_drift_repo=FeatureDriftMetricsRepository(db),
    )


class _HasRun(Protocol):
    name: str
    description: str

    async def run(self, **kwargs: Any) -> dict: ...


class _BaseTool:
    name: str = "tool"
    description: str = ""

    def _wrap_error(self, exc: Exception) -> dict:
        # Single place to log + project the error shape so the trace
        # entry the node emits is consistent across tools.
        logger.warning(
            "assist_agent_tool_failed",
            tool=self.name,
            error=str(exc),
        )
        return {"error": f"{type(exc).__name__}: {exc}"}


class CustomerPatternTool(_BaseTool):
    """Wraps `transaction_repo.customer_pattern_30d(customer_id)`.

    Returns the customer's 30-day transaction signature so the
    synthesizer can flag anomalies (e.g. txn 5x the customer's mean).
    """

    name = "customer_pattern"
    description = (
        "30-day customer transaction pattern (mean/stddev/top charge codes)"
    )

    def __init__(self, transaction_repo: Any) -> None:
        self._repo = transaction_repo

    async def run(self, *, customer_id: str | None = None, **_: Any) -> dict:
        if not customer_id:
            return {"error": "customer_id_required"}
        try:
            return await self._repo.customer_pattern_30d(customer_id)
        except Exception as exc:  # noqa: BLE001
            return self._wrap_error(exc)


class RuleTypeFrequencyTool(_BaseTool):
    """Wraps `case_repo.rule_type_frequency(rule_type, days=7)`.

    Tells the synthesizer how common this rule_type is week-over-week
    and how many distinct customers tripped it (helps decide whether
    we're seeing a one-off or a systemic issue).
    """

    name = "rule_type_frequency"
    description = "7-day frequency of a given rule_type across the queue"

    def __init__(self, case_repo: Any) -> None:
        self._repo = case_repo

    async def run(
        self, *, rule_type: str | None = None, days: int = 7, **_: Any
    ) -> dict:
        if not rule_type:
            return {"error": "rule_type_required"}
        try:
            return await self._repo.rule_type_frequency(rule_type, days=days)
        except Exception as exc:  # noqa: BLE001
            return self._wrap_error(exc)


class DriftSnapshotTool(_BaseTool):
    """Wraps `drift_repo.snapshot_for_features(feature_names)`.

    Maps a list of feature names → list of `{feature_name, severity,
    ks_statistic, drift_detected}`. Empty input = empty list (the
    analytics node skips the call when no rule cites a feature).
    """

    name = "drift_snapshot"
    description = "Latest drift status for the rule's evidence features"

    def __init__(self, drift_repo: Any) -> None:
        self._repo = drift_repo

    async def run(
        self, *, feature_names: list[str] | None = None, **_: Any
    ) -> dict:
        names = list(feature_names or [])
        if not names:
            return {"snapshots": []}
        try:
            snapshots = await self._repo.snapshot_for_features(names)
            return {"snapshots": list(snapshots or [])}
        except Exception as exc:  # noqa: BLE001
            return self._wrap_error(exc)


class VectorSearchTool(_BaseTool):
    """Wraps `RagService.assist(case_id, k, threshold)`.

    Reuses the existing RAG flow verbatim — same vector index, same
    metadata filter, same projection. The agent just consumes
    `similar_cases` from the response; it does NOT rely on the bundled
    LLM-synthesised `assist` payload (we re-synthesize via the
    classifier/synthesizer nodes so the trace stays attributable).
    """

    name = "vector_search"
    description = "Vector search over the resolved-case history corpus"

    def __init__(self, rag_service: Any) -> None:
        self._rag = rag_service

    async def run(
        self,
        *,
        case_id: str | None = None,
        k: int = 5,
        score_threshold: float = 0.70,
        **_: Any,
    ) -> dict:
        if not case_id:
            return {"error": "case_id_required"}
        try:
            return await self._rag.assist(
                case_id, k=k, score_threshold=score_threshold
            )
        except Exception as exc:  # noqa: BLE001
            return self._wrap_error(exc)
