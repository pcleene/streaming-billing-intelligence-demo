# Streaming Billing — ML / Pillar 4

Offline feature pipeline + training loop. Companion to `backend/app/services/feature_service.py`
(online feature serving) and `backend/app/workers/feature_engineer.py` (online rolling counters).

## Files

| File | Purpose |
| ---- | ------- |
| `feature_pipeline_and_training.ipynb` | PySpark structured streaming → Delta → IsolationForest + MLflow |
| `jobs/score_features.py` | Batch-score all `features` docs (scheduled job; same as notebook inference cell) |
| `jobs/promote_model.py` | Register an MLflow run → Production, **blocked** if drift is `alert` on `quarantine_iforest` inputs |

## Run

```sh
uv run jupyter lab ml/feature_pipeline_and_training.ipynb
```

The notebook reuses `backend/.env` for Mongo credentials. Delta tables land under
`./data/delta/` by default (override with `ACME_DELTA_ROOT`).

### Batch scoring (production-style)

From repo root, with the same MLflow run or a `.joblib` export wired in `backend/.env`:

```sh
PYTHONPATH=backend python ml/jobs/score_features.py
```

### Model promotion (drift-gated)

```sh
PYTHONPATH=backend python ml/jobs/promote_model.py --run-id <mlflow_run_id>
```

Use `--skip-drift-gate` only for emergencies. Exit code `2` means the drift gate blocked promotion.

## What runs where

| Layer | Component | Runtime |
| ----- | --------- | ------- |
| Online | rolling 1h/24h counters | `feature_engineer` worker |
| Offline | rolling 24h/7d windows | Spark notebook → Delta |
| Training | IsolationForest | sklearn + MLflow (notebook) |
| Batch serving | `$set` model_score on all customers | `ml/jobs/score_features.py` (cron / Airflow) |
| On-demand API | one customer | `POST /api/features/score/{customer_id}` (FastAPI) |
| Per-txn (optional) | score customer after each insert | `QUARANTINE_IFOREST_SCORE_ON_EACH_TRANSACTION=true` + `feature_engineer` |

The split exists because the heavy windows (7d, 30d, 90d) blow up the change-stream worker —
Spark+Delta was added specifically to keep them off the operational path.

## Backend env (`backend/.env`)

| Variable | Meaning |
| -------- | ------- |
| `QUARANTINE_IFOREST_ENABLED` | `true` to load the model in API + worker |
| `QUARANTINE_IFOREST_SCORE_ON_EACH_TRANSACTION` | `true` to score after every txn in `feature_engineer` (default `false`) |
| `QUARANTINE_IFOREST_MODEL_PATH` | Path to a `.joblib` / pickle of the fitted `IsolationForest` |
| `QUARANTINE_IFOREST_MLFLOW_RUN_ID` | Alternative: load `runs:/<id>/model` |
| `QUARANTINE_IFOREST_MLFLOW_TRACKING_URI` | MLflow store URI (default `file:./mlruns` relative to backend cwd) |
| `QUARANTINE_IFOREST_MODEL_VERSION` | Optional explicit version string stamped on writes |

## MLflow

Local file-store under `./mlruns`. Browse runs:

```sh
mlflow ui --backend-store-uri ./mlruns
```

The model version is the run-id prefix (`iforest_<8 hex>`) and is stamped on each
`features` document as `model_version`.
