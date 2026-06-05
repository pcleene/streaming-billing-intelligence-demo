#!/usr/bin/env python3
# ruff: noqa: E402
"""Batch-score every `features` row and write `model_score` / `model_version` (notebook cell 6).

Run from repo root after training:

  cd /path/to/repo && PYTHONPATH=backend python ml/jobs/score_features.py

Requires the same env as the backend (`backend/.env`) and a trained artifact
(`QUARANTINE_IFOREST_MODEL_PATH` or `QUARANTINE_IFOREST_MLFLOW_RUN_ID`).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "backend" / ".env")

import numpy as np
from pymongo import MongoClient, UpdateOne, WriteConcern  # noqa: E402

from app.config import Settings  # noqa: E402
from app.ml.quarantine_iforest import (  # noqa: E402
    IFOREST_FEATURE_COLUMNS,
    features_doc_to_matrix,
    load_quarantine_iforest_model,
    score_samples_negated,
)


def main() -> None:
    cfg = Settings()
    uri = cfg.mongodb_url
    db_name = cfg.acme_db
    x509 = cfg.tls_cert_path
    loaded = load_quarantine_iforest_model(
        model_path=cfg.quarantine_iforest_model_path,
        mlflow_run_id=cfg.quarantine_iforest_mlflow_run_id,
        mlflow_tracking_uri=cfg.quarantine_iforest_mlflow_tracking_uri,
        explicit_version=cfg.quarantine_iforest_model_version,
    )
    if loaded is None:
        raise SystemExit(
            "No model: set quarantine_iforest_model_path or quarantine_iforest_mlflow_run_id "
            "in backend/.env (or export QUARANTINE_IFOREST_*)."
        )
    model, version = loaded

    client_kwargs: dict = {}
    if x509:
        client_kwargs["tls"] = True
        client_kwargs["tlsCertificateKeyFile"] = x509
    client = MongoClient(uri, **client_kwargs)
    coll = client[db_name]["features"]
    proj = {"_id": 1, "customer_id": 1, **dict.fromkeys(IFOREST_FEATURE_COLUMNS, 1)}
    rows = list(coll.find({}, proj))
    if not rows:
        print("no feature rows")
        client.close()
        return

    mat = np.vstack([features_doc_to_matrix(r) for r in rows])
    scores = score_samples_negated(model, mat)
    now = datetime.now(UTC)
    ops = [
        UpdateOne(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "model_score": float(scores[i]),
                    "model_version": version,
                    "scored_at": now,
                }
            },
        )
        for i, doc in enumerate(rows)
    ]
    res = coll.with_options(write_concern=WriteConcern("majority")).bulk_write(ops, ordered=False)
    print("matched:", res.matched_count, "modified:", res.modified_count, "version:", version)
    client.close()


if __name__ == "__main__":
    main()
