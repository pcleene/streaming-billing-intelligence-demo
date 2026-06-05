"""Feature store schemas.

The `features` collection has one canonical, flat top-level shape that
EVERY writer (seeder, FE worker, Spark batch, scorer) must conform to.
Missing values are explicit `None` placeholders — never absent fields —
so readers (drift detector, iforest scorer, dashboard) can rely on a
stable schema regardless of which writer touched the doc most recently.

Single source of truth:
  * :data:`CANONICAL_FEATURE_FIELDS` — the canonical top-level field list.
  * :func:`canonical_feature_doc` — factory returning an empty doc with
    every canonical field present and a default value (None / 0 / [] /
    sub-doc) for each.

Field ownership (which writer is allowed to populate which field):
  * **seeder**          — profile-derived (`package_value_myr`,
    `spend_to_package_ratio`) and demo-warm-start values.
  * **feature_engineer** (online change-stream) — 5-min rolling
    (`txn_count_5m`, `amount_sum_5m`, `discount_sum_5m`), `last_txn_at`,
    `txn_count_total`, lineage min/max, ASP-derived `computed_signals`
    pass-through.
  * **Spark batch** (`windows-merge-to-mongo` cell + `infer-batch` cell)
    — offline windows (`txn_count_24h`, `txn_count_7d`, `spend_24h_myr`,
    `spend_7d_myr`, `discount_rate_7d`, `discount_rate_30d`,
    `quarantine_count_30d`) and model output (`model_score`,
    `model_version`, `scored_at`).

Sub-documents follow the PR-10 contract shape:
  * `lineage = {source_transactions_count, earliest_source_at,
    latest_source_at}`
  * `quality = {missing_inputs, outlier_flags, confidence,
    computed_via}` — `computed_via` records the most recent writer
    (`"seed" | "online" | "spark_batch"`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Canonical schema — one shape for every features doc.
# ---------------------------------------------------------------------------

CANONICAL_FEATURE_FIELDS: tuple[str, ...] = (
    # Identity / time
    "customer_id",
    "as_of",
    "created_at",
    "updated_at",
    "last_txn_at",
    "first_txn_at",
    # Versioning
    "feature_set_version",
    # 5-minute rolling — owner: ASP acme-feature-window-5m
    # ($hoppingWindow 5m / 30s hop, $merge into features)
    "txn_count_5m",
    "amount_sum_5m",
    "discount_sum_5m",
    # Offline windows — owner: Spark batch (windows-merge-to-mongo cell)
    "txn_count_1h",
    "txn_count_24h",
    "txn_count_7d",
    "spend_24h_myr",
    "spend_7d_myr",
    "discount_rate_7d",
    "discount_rate_30d",
    "quarantine_count_30d",
    # Profile-derived — owner: seeder / onboarding job
    "package_value_myr",
    "spend_to_package_ratio",
    # ASP / FE computed signals — owner: feature_engineer worker
    "txn_velocity_5m",
    "amount_vs_avg_30d_pct",
    "discount_pct_vs_avg_30d",
    "is_high_risk_charge_code",
    # Raw counters — owner: feature_engineer worker
    "txn_count_total",
    # Model output — owner: Spark infer-batch cell + scorer service
    "model_score",
    "model_version",
    "scored_at",
)


# Default value per canonical field. None for any value-bearing field
# the writer doesn't own; 0 only for accumulators where 0 is the
# unambiguous starting point.
_NUMERIC_ACCUMULATORS: frozenset[str] = frozenset({"txn_count_total"})


FEATURE_SET_VERSION_CURRENT: str = "v3.2.1"


def canonical_feature_doc(
    customer_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Empty default features doc with every canonical field present.

    Every writer should pass this (minus the keys it actively `$set`s)
    into `$setOnInsert` so a brand-new doc has the full canonical shape
    even when only one writer has touched the row so far.

    `now` is injectable so test pinning of `created_at` / `updated_at`
    stays deterministic.
    """
    ts = now or datetime.now(timezone.utc)
    doc: dict = {}
    for field in CANONICAL_FEATURE_FIELDS:
        if field == "customer_id":
            doc[field] = customer_id
        elif field == "created_at":
            doc[field] = ts
        elif field == "updated_at":
            doc[field] = ts
        elif field == "feature_set_version":
            doc[field] = FEATURE_SET_VERSION_CURRENT
        elif field in _NUMERIC_ACCUMULATORS:
            doc[field] = 0
        else:
            doc[field] = None
    doc["lineage"] = {
        "source_transactions_count": 0,
        "earliest_source_at": None,
        "latest_source_at": None,
    }
    doc["quality"] = {
        "missing_inputs": [],
        "outlier_flags": [],
        "confidence": 1.0,
        "computed_via": "online",
    }
    return doc


# ---------------------------------------------------------------------------
# Pydantic models — kept thin; the canonical doc factory is the source
# of truth for shape, these models are for response-typing only.
# ---------------------------------------------------------------------------


class FeatureVector(BaseModel):
    """Per-customer engineered features."""
    model_config = ConfigDict(populate_by_name=True)

    customer_id: str

    # Rolling counts
    txn_count_1h: int = 0
    txn_count_24h: int = 0
    txn_count_7d: int = 0

    # Rolling sums
    spend_24h_myr: float = 0.0
    spend_7d_myr: float = 0.0

    # Discount metrics
    discount_rate_30d: float = 0.0    # discount_amount / amount
    quarantine_count_30d: int = 0

    # Time-since
    minutes_since_last_txn: float | None = None
    minutes_since_last_quarantine: float | None = None

    # Profile-derived
    package_value_myr: float = 0.0
    spend_to_package_ratio: float = 0.0

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FeatureFreshness(BaseModel):
    """For the dashboard freshness tile."""
    customer_id: str
    updated_at: datetime
    age_seconds: float


class StreamLagSnapshot(BaseModel):
    """Time delta between Mongo write and Delta append (Pillar 4 lag tile)."""
    measured_at: datetime
    lag_seconds: float
    sample_size: int


# --- Phase B.5 — feature drift detector ---------------------------------

DriftSeverity = Literal["none", "watch", "warn", "alert"]


class DistributionStats(BaseModel):
    """Summary statistics over a population of feature samples."""
    n: int
    mean: float
    std: float
    min: float
    p25: float
    p50: float
    p75: float
    p99: float
    max: float


class FeatureDriftMetric(BaseModel):
    """One detector tick for a single (feature_name) on numeric features.

    `current` is the trailing 24-hour window; `baseline` is a 30-day frozen
    reference. KS test compares the two and severity is binned on the
    statistic.

    PR-1 extensions per the plan §B.5:
      - `affected_consumers` — model names + rule types that read this feature.
      - `recommended_action` — which on-call action to take.
      - `severity_progression` — append-only history of severity escalation.
      - `model_lineage` — which model version was trained on this feature.
    """
    model_config = ConfigDict(populate_by_name=True)

    feature_name: str
    measured_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    severity: DriftSeverity = "none"
    ks_statistic: float = 0.0
    p_value: float = 1.0
    current: DistributionStats
    baseline: DistributionStats
    sample_size_current: int = 0
    sample_size_baseline: int = 0

    # PR-1 / PR-10 extensions
    affected_consumers: list[str] = Field(default_factory=list)
    recommended_action: Literal[
        "monitor", "investigate", "retrain", "alert_oncall",
    ] = "monitor"
    severity_progression: list[dict] = Field(default_factory=list)
    model_lineage: list[str] = Field(default_factory=list)
    drift_detected: bool = False
    investigation_link: str | None = None


# --- Lineage / quality blocks for the features document (PR-10) ----------

class FeatureLineage(BaseModel):
    source_transactions_count: int = 0
    earliest_source_at: datetime | None = None
    latest_source_at: datetime | None = None


class FeatureQuality(BaseModel):
    missing_inputs: list[str] = Field(default_factory=list)
    outlier_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    computed_via: Literal["seed", "online", "spark_batch"] = "online"
