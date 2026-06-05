"""IsolationForest feature vector + model loading (Pillar 4).

Training uses the same column order as `ml/feature_pipeline_and_training.ipynb`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# Consumer id persisted on drift docs' `affected_consumers` for promotion gating.
MODEL_CONSUMER_ID = "quarantine_iforest"

IFOREST_FEATURE_COLUMNS: tuple[str, ...] = (
    "txn_count_1h",
    "txn_count_24h",
    "txn_count_7d",
    "spend_24h_myr",
    "spend_7d_myr",
    "discount_rate_30d",
    "quarantine_count_30d",
    "package_value_myr",
    "spend_to_package_ratio",
)


def features_doc_to_matrix(doc: dict[str, Any]) -> np.ndarray:
    """One row (1, n_features) for sklearn, NaN → 0."""
    row: list[float] = []
    for col in IFOREST_FEATURE_COLUMNS:
        v = doc.get(col)
        if v is None:
            row.append(0.0)
        else:
            try:
                row.append(float(v))
            except (TypeError, ValueError):
                row.append(0.0)
    return np.asarray([row], dtype=np.float64)


def score_samples_negated(model: Any, X: np.ndarray) -> np.ndarray:
    """Match notebook convention: higher = more anomalous."""
    raw = model.score_samples(X)
    return -np.asarray(raw, dtype=np.float64)


def load_quarantine_iforest_model(
    *,
    model_path: str | None,
    mlflow_run_id: str | None,
    mlflow_tracking_uri: str,
    explicit_version: str | None,
) -> tuple[Any, str] | None:
    """Return (sklearn_model, version_label) or None if nothing to load."""
    if mlflow_run_id:
        import mlflow

        mlflow.set_tracking_uri(mlflow_tracking_uri)
        model = mlflow.sklearn.load_model(f"runs:/{mlflow_run_id}/model")
        ver = explicit_version or f"iforest_{mlflow_run_id[:8]}"
        return model, ver
    if model_path:
        import joblib

        path = Path(model_path)
        model = joblib.load(path)
        ver = explicit_version or path.stem
        return model, ver
    return None
