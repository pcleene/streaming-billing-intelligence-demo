# Changelog

Concise summary of feature delivery from PR-9 through PR-15. Earlier
PRs (PR-1 through PR-8) are covered by the dated handoff documents in
this directory.

## PR-15 — Cleanup (current)

- Replaced all `datetime.utcnow()` callsites with timezone-aware
  `datetime.now(timezone.utc)` (16 callsites across repos, services,
  workers).
- Declared `langgraph` as an explicit backend dependency (was a
  transitive import from PR-AG); declared `@playwright/test` as an
  explicit frontend dev dependency.
- Consolidated agent test directories under
  `backend/tests/test_services/test_assist_agent/` (verify).
- Fixed pre-existing `svelte-check` tautologies and `page.params`
  narrowing warnings surfaced by the latest svelte-check run.
- Added this CHANGELOG plus a "Recent changes" pointer in `README.md`.

## PR-14 — Seeds

- Six new / rewritten seed modules orchestrated by
  `backend/scripts/seed.py`: `seed_customers_commercial`,
  `seed_quarantine_cases`, `seed_system_metrics`, `seed_features`,
  `seed_feature_drift_metrics`, `seed_bill_cycles`.
- `seed.py` gained `--skip-pr14-seeds`, `--quarantine-cases`,
  `--commercial-parents`, `--feature-docs`, `--burst-samples`,
  `--steady-samples` flags.
- Commercial-customer seed builds parents + outlets to exercise the
  bucketed `recent_transactions` path.
- `system_metrics` seed produces both burst-window and steady-state
  rows so the Burst tile renders without a live simulator run.
- `feature_drift_metrics` seed populates the Drift tile with
  watch/warn/alert progression rows.
- Order-of-operations documented in `seed.py`: commercial customers
  before cycles + features; cases reference customers; drift
  references features.

## PR-13 — Frontend

- Added the rich quarantine-case detail page
  (`frontend/src/routes/quarantine/[id]/+page.svelte`) with
  `AssistPanel`, `AgentTracePanel`, `BeforeAfterPanel`,
  `ImpactAnalysisPanel`, `InvestigateActionForm`,
  `TransactionPatternPanel`.
- Added `BurstModeTile`, `BurstSampleSparkline`,
  `BurstSummaryStrip` for the metrics page, plus
  `FeatureDriftTile` / `DriftStatusCard` for the drift surface.
- Added `NextBestOfferCard`, `CustomerRefreshPanel`,
  `EmbeddingStatusBadge`, `ConfidenceBar` to the customer 360
  surface; `ParamField` shared form input.
- Added `quarantine/batch-assist`, `features/[name]`, and `metrics`
  routes; layout updated to expose the new pages.
- Playwright smoke fixtures wired against the seeded personas
  (verify).

## PR-12 — Services + routes

- New services: `nbo_service.py`, `before_after_service.py`,
  `transaction_analytics_service.py`, `drift_telemetry_service.py`,
  `customer_refresh_service.py`, `metrics_refresh_service.py`,
  `agent_observability_service.py`.
- New routes: `before_after.py`, `drift.py`, `metrics.py`,
  `system_metrics.py`, `analyst.py`; existing `customers.py`,
  `quarantine.py`, `rules.py`, `features.py` extended with the
  PR-12 endpoints.
- Customer dispatch flows through `customer_index` from the legacy
  `/api/customers/{customer_id}` route to the typed residential /
  commercial collections.
- Hybrid search builder (`search_builder.py`) extended for vector +
  text + segment filters across `customers_*` (verify).

## PR-AG — Agentic AI assist (LangGraph)

- New `backend/app/services/assist_agent/` package with
  `graph.py` (LangGraph state machine), `nodes.py`,
  `state.py`, `tools.py`, `agent.py`, `defaults.py`.
- Tools: `CustomerPatternTool`, `DriftSnapshotTool`,
  `RuleTypeFrequencyTool`, `VectorSearchTool` — all read-only,
  composing the existing repos.
- Public surface: `AssistAgent`, `build_graph` /
  `build_agent_graph` alias, `AssistAgentDeps`,
  `AssistAgentState`, `AgentTraceEntry`, `CaseClassification`,
  `AssistAgentRunSummary`.
- New worker `app/workers/assist_agent_worker.py` tails
  `quarantine_cases` change-stream for `insert` events and
  dispatches `AssistAgent.run(case_id=...)` through a bounded
  `asyncio.Semaphore`; gated by `flags.AI_ASSIST_AGENTIC`.
- Trace rolled up into `AssistAgentRunSummary` and persisted by
  the worker; the graph itself does no DB writes so runs are
  idempotent.

## PR-11 — System metrics + burst mode

- `app/workers/transaction_simulator.py` extended with `--burst`,
  `--burst-target-tps`, `--burst-duration-seconds`; events stamped
  with `burst_run_id` / `burst_phase` metadata.
- `app/workers/metrics_recorder.py` added — writes one
  `system_metrics` row per minute with TTL on `measured_at`.
- `metrics_aggregator_service.get_burst_status(run_id?)` returns
  the latest rows for a burst run.
- TTL index on `system_metrics.measured_at` declared in
  `setup_indexes.py`.

## PR-10 — Feature engineer + drift detector

- `app/workers/feature_engineer.py` rewritten to prefer
  `transactions.computed_signals` over rolling recomputation; falls
  back to the rolling logic only for windows not pre-computed.
- `$set customers.latest_features` after every features write so
  the 360 read stays a single document.
- `app/workers/feature_drift_detector.py` added — 15-minute KS
  statistic vs frozen 30-day baseline; persists to
  `feature_drift_metrics` with `affected_consumers`,
  `recommended_action`, `severity_progression`, `model_lineage`.
- `feature_repo.py` extended with `lineage` and `quality` writes.
- Indexes added: `(customer_id, as_of desc)` on `features`,
  `(feature_name, measured_at desc)` on `feature_drift_metrics`.

## PR-9 — Customer 360 deep enrichment

- New services: `customer_360_service.py` (composition layer, no
  joins), `embedding_service.py` (Voyage-4 wrapper —
  `embed_customer(doc)` returns 1024-d vector + `embedding_text`),
  `metrics_aggregator_service.py` (cross-entity metrics rollups
  including 12-month LTV / monthly_spend / viewing_hours / ppv
  trends).
- New workers: `customer_360_aggregator.py` (nightly recompute of
  `cross_entity_metrics`, `interaction_history.channel_engagement_rates`,
  `current_cycle`); `embedding_refresher.py` (delta-threshold
  re-embedding with per-customer cooldown).
- `customer_repo.py` gained `push_brand_journey_event`,
  `push_support_interaction` (`$slice:-20`),
  `push_marketing_interaction` (`$slice:-50`),
  `set_active_campaign_status`, `set_equipment_status`.
- `backend/scripts/enrich_customers_360.py` — one-time idempotent
  migrator with `--dry-run` and `--batch-size`.
- Atlas Vector Search indexes on `customers_residential.embedding`
  and `customers_commercial.embedding` declared in
  `setup_indexes.py`.
- Behaviour gated by `FeatureFlags.RICH_CUSTOMER_360` until PR-15
  removed the flag.
