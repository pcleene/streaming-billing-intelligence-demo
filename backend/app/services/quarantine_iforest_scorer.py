"""Load/serve the quarantine IsolationForest model (API + worker)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings, settings
from app.core.constants import FEATURES
from app.core.logging import get_logger
from app.ml.quarantine_iforest import (
    IFOREST_FEATURE_COLUMNS,
    features_doc_to_matrix,
    load_quarantine_iforest_model,
    score_samples_negated,
)

logger = get_logger(__name__)

_scorer: QuarantineIforestScorer | None = None


class QuarantineIforestScorer:
    def __init__(self, model: Any, model_version: str) -> None:
        self._model = model
        self._version = model_version

    @property
    def model_version(self) -> str:
        return self._version

    def _score_matrix_sync(self, X) -> Any:
        return score_samples_negated(self._model, X)

    async def score_customer(
        self,
        db: AsyncDatabase,
        customer_id: str,
    ) -> dict[str, Any] | None:
        proj = {"_id": 0, "customer_id": 1, **dict.fromkeys(IFOREST_FEATURE_COLUMNS, 1)}
        doc = await db[FEATURES].find_one({"customer_id": customer_id}, proj)
        if not doc:
            return None
        X = features_doc_to_matrix(doc)
        scores = await asyncio.to_thread(self._score_matrix_sync, X)
        score = float(scores[0])
        now = datetime.now(UTC)
        await db[FEATURES].update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "model_score": score,
                    "model_version": self._version,
                    "scored_at": now,
                    "updated_at": now,
                }
            },
        )
        return {
            "customer_id": customer_id,
            "model_score": score,
            "model_version": self._version,
            "scored_at": now.isoformat(),
        }


def build_scorer_from_settings(cfg: Settings | None = None) -> QuarantineIforestScorer | None:
    cfg = cfg or settings
    if not cfg.quarantine_iforest_enabled:
        return None
    loaded = load_quarantine_iforest_model(
        model_path=cfg.quarantine_iforest_model_path,
        mlflow_run_id=cfg.quarantine_iforest_mlflow_run_id,
        mlflow_tracking_uri=cfg.quarantine_iforest_mlflow_tracking_uri,
        explicit_version=cfg.quarantine_iforest_model_version,
    )
    if loaded is None:
        logger.warning(
            "quarantine_iforest_enabled_but_no_model",
            hint="set QUARANTINE_IFOREST_MODEL_PATH or QUARANTINE_IFOREST_MLFLOW_RUN_ID",
        )
        return None
    model, ver = loaded
    logger.info("quarantine_iforest_model_loaded", model_version=ver)
    return QuarantineIforestScorer(model, ver)


def init_scorer(cfg: Settings | None = None) -> None:
    """Call from FastAPI lifespan and from `feature_engineer` worker startup."""
    global _scorer
    _scorer = build_scorer_from_settings(cfg)


def get_scorer() -> QuarantineIforestScorer | None:
    return _scorer


async def score_customer_if_configured(
    db: AsyncDatabase,
    customer_id: str,
    cfg: Settings | None = None,
) -> None:
    """After each transaction: score one customer when both flags are on."""
    cfg = cfg or settings
    if not cfg.quarantine_iforest_enabled:
        return
    if not cfg.quarantine_iforest_score_on_each_transaction:
        return
    scorer = get_scorer()
    if scorer is None:
        return
    try:
        await scorer.score_customer(db, customer_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "quarantine_iforest_online_score_failed",
            customer_id=customer_id,
            error=str(exc),
        )
