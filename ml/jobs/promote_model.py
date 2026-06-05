#!/usr/bin/env python3
# ruff: noqa: E402
"""Register an MLflow run as a model version and move it to Production — gated on drift.

  PYTHONPATH=backend python ml/jobs/promote_model.py --run-id <run_id> \\
      [--registered-model streaming_billing_quarantine_iforest]

Exits non-zero when any latest `feature_drift_metrics` row is `severity=alert`
for consumer `quarantine_iforest`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / "backend" / ".env")

import mlflow  # noqa: E402
from mlflow.tracking import MlflowClient  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.ml.drift_gate import DriftGateError, assert_no_blocking_drift  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", required=True, help="MLflow run id that logged sklearn model")
    p.add_argument(
        "--registered-model",
        default="streaming_billing_quarantine_iforest",
        help="MLflow registered model name",
    )
    p.add_argument(
        "--skip-drift-gate",
        action="store_true",
        help="Emergency override (not recommended)",
    )
    args = p.parse_args()

    cfg = Settings()
    client_kwargs: dict = {}
    if cfg.tls_cert_path:
        client_kwargs["tls"] = True
        client_kwargs["tlsCertificateKeyFile"] = cfg.tls_cert_path
    mclient = MongoClient(cfg.mongodb_url, **client_kwargs)
    if not args.skip_drift_gate:
        try:
            assert_no_blocking_drift(mclient, cfg.acme_db)
        except DriftGateError as exc:
            print("DRIFT GATE BLOCKED:", exc)
            mclient.close()
            return 2
    mclient.close()

    tracking = cfg.quarantine_iforest_mlflow_tracking_uri or os.environ.get(
        "MLFLOW_TRACKING_URI", "file:./mlruns"
    )
    mlflow.set_tracking_uri(tracking)
    name = args.registered_model
    src = f"runs:/{args.run_id}/model"
    try:
        mv = mlflow.register_model(model_uri=src, name=name)
    except Exception as exc:  # noqa: BLE001
        print("register_model failed:", exc)
        return 3
    mlflow_client = MlflowClient()
    mlflow_client.transition_model_version_stage(
        name,
        mv.version,
        stage="Production",
        archive_existing_versions=True,
    )
    print("promoted", name, "version", mv.version, "run", args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
