"""Centralised constants — collection names, index names, topic names.

Never hardcode these inline anywhere else in the codebase.
"""

from __future__ import annotations

from typing import Final


# --- Collections ----------------------------------------------------------
# `CUSTOMERS` remains as a thin alias for `CUSTOMERS_RESIDENTIAL` for the
# many call sites that don't distinguish residential vs commercial.
CUSTOMERS_RESIDENTIAL: Final[str]     = "customers_residential"
CUSTOMERS_COMMERCIAL: Final[str]      = "customers_commercial"
CUSTOMER_INDEX: Final[str]            = "customer_index"
CUSTOMERS: Final[str]                 = CUSTOMERS_RESIDENTIAL

TRANSACTIONS: Final[str]              = "transactions"
QUARANTINE_RULES: Final[str]          = "quarantine_rules"
QUARANTINE_CASES: Final[str]          = "quarantine_cases"
QUARANTINE_CASES_HISTORY: Final[str]  = "quarantine_cases_history"
FEATURES: Final[str]                  = "features"
CRM_SNAPSHOTS: Final[str]             = "crm_snapshots"
SYSTEM_METRICS: Final[str]            = "system_metrics"
FEATURE_DRIFT_METRICS: Final[str]     = "feature_drift_metrics"
BILL_CYCLES: Final[str]               = "bill_cycles"
CHARGE_CODES: Final[str]              = "charge_codes"

ALL_COLLECTIONS: Final[tuple[str, ...]] = (
    CUSTOMERS_RESIDENTIAL, CUSTOMERS_COMMERCIAL, CUSTOMER_INDEX,
    TRANSACTIONS,
    QUARANTINE_RULES, QUARANTINE_CASES, QUARANTINE_CASES_HISTORY,
    FEATURES, CRM_SNAPSHOTS, SYSTEM_METRICS, FEATURE_DRIFT_METRICS,
    BILL_CYCLES, CHARGE_CODES,
)


# --- Atlas Search / Vector indexes ----------------------------------------
# Vector search runs only against `quarantine_cases_history` (the RAG
# corpus) via Atlas Auto Embedding (ADR-032). The legacy BYO-Voyage
# `case_history_vector_idx` was retired in PR-A — see
# `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for the
# pre-AutoEmbed shape if a revert is ever needed. The single AutoEmbed
# index points at the `embed_source.text` leaf string and stores the
# vector invisibly; Voyage credentials live in Atlas project settings.
# `customers_search_idx` is a regular Atlas Search index, not a vector
# index.
IDX_CUSTOMERS_SEARCH: Final[str]       = "customers_search_idx"
IDX_CASE_HISTORY_AUTOEMBED: Final[str] = "case_history_autoembed_idx"
# AutoEmbed vector indexes on the typed customer collections — Atlas
# reads `embed_source.text` on each customer document and stores the
# vector invisibly. Powers the natural-language "find me a customer
# like…" search at `GET /api/customers/search`.
IDX_CUSTOMERS_RESIDENTIAL_AUTOEMBED: Final[str] = "customers_residential_autoembed_idx"
IDX_CUSTOMERS_COMMERCIAL_AUTOEMBED: Final[str]  = "customers_commercial_autoembed_idx"

# Embed-source path constants — the AutoEmbed index points here.
EMBED_SOURCE_PARENT: Final[str] = "embed_source"
EMBED_SOURCE_PATH: Final[str]   = "embed_source.text"


# --- ASP processor names --------------------------------------------------
ASP_PROCESSORS: Final[tuple[str, ...]] = (
    "acme-event-ingest",
    "acme-feature-rolling-writer",
    "acme-feature-window-5m",
    "acme-rule-discount-mismatch",
    "acme-rule-velocity-anomaly",
    "acme-rule-entitlement-mismatch",
    "acme-rule-geographic-anomaly",
    "acme-rule-duplicate-transaction",
    # Phase B.2 — Acme-named rules
    "acme-rule-termination-fee-check",
    "acme-rule-unearned-earned-segregation",
    "acme-rule-double-charge-multi-code",
    "acme-rule-proration-check",
)


# --- Demo metric thresholds ----------------------------------------------
P99_TARGET_MS: Final[int] = 200
THROUGHPUT_TARGET_TPS: Final[int] = 100


# --- SSE event names ------------------------------------------------------
SSE_NEW_CASE: Final[str]    = "new_case"
SSE_CASE_UPDATE: Final[str] = "case_update"
SSE_RULE_CHANGE: Final[str] = "rule_change"
SSE_METRIC_TICK: Final[str] = "metric_tick"
SSE_NEW_TXN: Final[str]     = "new_txn"


# --- Schema versioning ----------------------------------------------------
# Current target schema version after PR-1 through PR-9. Documents written
# in the new shape MUST stamp `_schema_version = SCHEMA_VERSION_V3`. Migration
# scripts compare against this constant. Validators are at validationLevel
# `moderate` until PR-15 promotes them to `strict`.
SCHEMA_VERSION_V3: Final[int] = 3
# V4 adds the `embed_source.text` shape used by Atlas Auto Embedding.
# Documents written via the AutoEmbed write path stamp `_schema_version=4`.
SCHEMA_VERSION_V4: Final[int] = 4


# --- Misc ----------------------------------------------------------------
MALAYSIA_TZ: Final[str] = "Asia/Kuala_Lumpur"
DEFAULT_PAGE_SIZE: Final[int] = 50
