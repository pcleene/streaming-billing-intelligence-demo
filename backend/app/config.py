"""Centralised settings — pydantic-settings reads from .env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- MongoDB Atlas -----------------------------------------------------
    mongodb_url: str = Field(
        default="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/<db>"
                "&authMechanism=MONGODB-X509&appName=testCluster"
    )
    tls_cert_path: str | None = "<local-path>"
    acme_db: str = "streaming_billing"

    # --- Atlas Stream Processing ------------------------------------------
    asp_uri: str = ""
    asp_kafka_connection: str = "UtilitymskKafkaConnection"
    asp_atlas_connection: str = "FuelRetail_cluster"

    # --- AWS / MSK --------------------------------------------------------
    aws_region: str = "ap-southeast-1"
    kafka_bootstrap_servers: str = (
        "<msk-broker>:9098,"
        "<msk-broker>:9098"
    )
    kafka_topic: str = "acme-billing-events"
    kafka_topic_partitions: int = 3
    kafka_topic_replication: int = 2

    # --- Atlas AutoEmbed (ADR-032) ---------------------------------------
    # The Voyage credential is configured at the Atlas project level
    # (Atlas → Project → Settings → Embedding Model Providers), NOT in
    # this file. The legacy `voyage_*` settings were removed in PR-A —
    # see `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for
    # the pre-AutoEmbed settings if a revert is ever needed.

    # --- Bedrock ---------------------------------------------------------
    bedrock_region: str = "ap-southeast-1"
    bedrock_model_id: str = "apac.anthropic.claude-sonnet-4-20250514-v1:0"
    bedrock_max_tokens: int = 1024

    # --- Server ----------------------------------------------------------
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    log_level: str = "INFO"

    # --- Quarantine IsolationForest (Pillar 4) ---------------------------
    # Loads a trained sklearn model for `POST /api/features/score/{id}` and
    # optional per-transaction scoring in `feature_engineer`.
    quarantine_iforest_enabled: bool = False
    quarantine_iforest_score_on_each_transaction: bool = False
    quarantine_iforest_model_path: str | None = None
    quarantine_iforest_mlflow_run_id: str | None = None
    quarantine_iforest_mlflow_tracking_uri: str = "file:./mlruns"
    quarantine_iforest_model_version: str | None = None

    # --- Demo knobs -----------------------------------------------------
    simulator_tps: int = 20
    simulator_anomaly_rate: float = 0.05
    crm_lag_hours: int = 4

    # --- Burst mode (Phase B.3) -----------------------------------------
    # Defaults match end-of-month billing-day spike profile.
    burst_target_tps: int = 200
    burst_duration_seconds: int = 300
    burst_ramp_seconds: int = 30
    metrics_recorder_interval_seconds: int = 60
    system_metrics_ttl_days: int = 7

    # --- CORS -----------------------------------------------------------
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
