# Architecture Decision Records — Streaming Billing

Lightweight ADRs for non-obvious design choices. Each entry: context → decision → consequence.

---

## ADR-001 — Reuse Fuel Retail-Demo Atlas + MSK infrastructure

**Context.** Streaming Billing demo runs in the same demo environment as the existing
FuelRetail-Demo demo. Spinning up a new Atlas cluster, MSK cluster, and ASP workspace
just for this demo is wasteful and risks misconfiguration.

**Decision.** Point at the same Atlas cluster (`<atlas-cluster>.mongodb.net`)
and MSK bootstrap brokers. Create a *new database* (`streaming_billing`), a *new
Kafka topic* (`acme-billing-events`), and *new ASP processors* (prefixed
`acme-`) within those existing resources. The ASP connection registry entries
`UtilitymskKafkaConnection` (Kafka source) and `FuelRetail_cluster` (Atlas sink) are
re-used because they are cluster-scoped — they can write to any database in the
target cluster, including `streaming_billing`.

**Consequence.** Zero new infrastructure to provision. Failure mode: a misconfigured
processor could touch `FuelRetail_fraud` collections. Mitigation: every processor
pipeline `$merge`s into `streaming_billing.<collection>` explicitly; processors are
named with the `acme-` prefix.

---

## ADR-002 — PyMongo `AsyncMongoClient`, not Motor

**Context.** The reference FuelRetail-Demo backend uses Motor. The product prompt for
this demo explicitly forbids Motor: PyMongo 4.9+ ships native async support,
and Motor is now deprecated upstream.

**Decision.** All backend MongoDB I/O uses
`from pymongo import AsyncMongoClient` and the async PyMongo collection API.
No Motor dependency anywhere in `backend/`. Repositories wrap PyMongo Async;
nothing above the repository layer touches PyMongo directly.

**Consequence.** Cleaner dependency tree, fewer transitive packages, and a
forward-looking codebase. Costs us copy-paste reuse from FuelRetail-Demo — the
patterns are identical but the import lines differ.

---

## ADR-003 — Backend layering is non-negotiable

**Context.** The FuelRetail-Demo backend uses a flat layout (`routers/`, `services/`,
`models/`, `kafka/`, `stream_processing/`). For Acme the prompt mandates a
layered structure (routes → services → repositories → pipelines → MongoDB).

**Decision.** Adopt the layered structure verbatim. Routes are HTTP-thin (≤10
lines per endpoint body). Services orchestrate. Repositories own MongoDB I/O.
Pipelines (aggregation builders) live in `app/pipelines/`. Streaming code lives
in `app/streaming/`. LLM code lives in `app/llm/`. Workers are separate process
entry points (`python -m app.workers.<name>`), not spun up inside the FastAPI
app.

**Consequence.** Higher initial scaffolding cost; clean seams pay off as soon
as we add Pillar 2's Rule Studio and Pillar 3's RAG flows that share the
SearchPipelineBuilder.

---

## ADR-004 — Composable `SearchPipelineBuilder` for vector + metadata + lookup

**Context.** Vector search + metadata filter + customer-context `$lookup`
appears in three flows: RAG case retrieval (Pillar 3), test-rule-against-history
(Pillar 2), analyst case search (Pillar 4 dashboard).

**Decision.** Implement once, in `app/pipelines/search_builder.py`, as a
fluent builder. Services compose stages; repositories execute. No service
constructs aggregation pipelines inline.

**Consequence.** A single place to change vector-search index naming, score
thresholds, or filter strategy. Trade-off: a level of indirection between the
service and the raw aggregation — this is a feature, not a bug, for a system
where pipelines will evolve.

---

## ADR-005 — Voyage embedding `input_type` consistency

**Context.** Voyage's `voyage-4-large` accepts `input_type` of either `"document"`
or `"query"`, which biases the embedding for retrieval. If the corpus is embedded
with `"document"` and the query with `"query"`, vectors live in *different*
sub-spaces and similarity scores are unreliable.

**Decision.** Use **`input_type="document"`** for **both** corpus indexing and
live query embedding. This trades a small theoretical retrieval-quality gain
(from query-tuned embeddings) for guaranteed shared vector-space comparability.
Configured via `VOYAGE_INPUT_TYPE=document` in `.env`. The
`embedding_service.embed()` method does not accept an override — there is exactly
one input type for the whole system.

**Consequence.** Corpus + query embeddings are always comparable. Adding a
new corpus (e.g. import historical FuelRetail-Demo cases) requires no re-embedding
because the same model + dims + input_type are used everywhere. Future change:
if we ever switch to `query`-tuned querying, we must re-embed the entire corpus.

---

## ADR-006 — Asymmetric `quarantine_rules` schema with discriminator

**Context.** Rule types genuinely have different parameter shapes. A
`velocity_anomaly` rule needs `window_seconds` and `max_transactions`; a
`discount_mismatch` rule needs `crm_promotion_field` and `grace_period_minutes`.
Forcing them into a uniform table (SQL-style) requires either many sparse
columns or a generic `parameters_json` blob — both lose validation.

**Decision.** One collection, one document per rule, with shared core fields
(`name`, `severity`, `enabled`, `mode`, `version`, `metrics`) and a polymorphic
`parameters` sub-document whose shape varies by `rule_type`. Pydantic v2
discriminated unions (`Field(discriminator="rule_type")`) validate the right
shape per type at the API boundary. JSON Schema validator on the collection
mirrors the same constraints at the database boundary.

**Consequence.** Adding a new rule type requires: (1) a new Pydantic
`*Parameters` model in `schemas/rule.py`, (2) a new builder function in
`pipelines/rule_pipeline_builders.py`, (3) a new Svelte form component in the
Rule Studio. No collection migration. This is the polymorphic pattern from the
MongoDB schema-design playbook applied to a real operational use case.

---

## ADR-007 — Rules activated via change stream, not polling

**Context.** The Rule Studio inserts/updates rule documents. The streaming
pipeline must pick up changes without redeployment.

**Decision.** A separate worker
(`app/workers/rule_change_watcher.py`) watches `quarantine_rules` via a
change stream and publishes activation events to MSK on a `rules-control` topic.
ASP processors subscribe to that topic and rebuild their in-flight pipeline
composition on receipt. (For the demo, the rules are also re-read at processor
start, so a manual `sp.restart()` activates a rule synchronously if needed.)

**Consequence.** Near-instant rule activation in the live demo without
restarting any service. Trade-off: a small operational complexity in the watcher
worker. Acceptable for the demo and a clean production pattern.

---

## ADR-008 — Spark Structured Streaming for feature pipeline (not batch)

**Context.** The Pillar 4 demo moment is "watch features land in Delta in
seconds, not nightly". A `spark.read.format("mongodb")` one-shot batch read
defeats that story.

**Decision.** The notebook uses `spark.readStream.format("mongodb")` (which
internally wraps Change Streams) → lightweight transforms → `writeStream
.format("delta")` with a `processingTime=5 seconds` trigger and a persistent
checkpoint location. **A separate** notebook cell does the *batch* read of the
Delta table for training. Streaming and training are decoupled by design.

**Consequence.** Demo moment is real (feature freshness measured in seconds,
visible in the streaming-lag tile on the dashboard). Constraint: requires a
replica set on the source — Atlas always provides one, so no constraint in
practice. Checkpoint must persist across notebook restarts — we use a fixed
local path the demo doesn't wipe.

---

## ADR-009 — No regex-based MongoDB queries

**Context.** Reflexive use of `$regex` on small/non-indexed string fields
results in collection scans and unbounded query latency.

**Decision.** Customer search uses **Atlas Search** (`$search` with autocomplete
+ fuzzy) for partial-match name/IC/account-id lookups. Equality queries are
`$eq` / `$in`. Regex is used only when there is a genuinely case-sensitive,
fully-anchored need (none in the current schema).

**Consequence.** Faster search at higher cardinality, sub-50ms typical lookup,
graceful behaviour as the customer collection grows past 10k.

---

## ADR-010 — Workers as separate processes, not in-process tasks

**Context.** The simulator, feature engineer, and rule change watcher are
long-running processes that should not share the FastAPI event loop. Running
them inside the FastAPI lifespan (as FuelRetail-Demo does for its watcher) couples
their failures to the API's availability and complicates scaling.

**Decision.** Each worker is a separate `python -m app.workers.<name>`
entrypoint, run as its own docker-compose service (or via `make simulator`,
`make features`, `make rules-watcher`). The FastAPI app does run a *thin*
SSE-broadcasting watcher in-process (mirroring FuelRetail-Demo) to power the
dashboard's real-time tiles — that watcher consumes change-stream events
already written by the upstream workers / ASP.

**Consequence.** Cleaner failure domains; each worker can be restarted
independently; horizontal scaling is straightforward. Trade-off: more compose
services to manage (mitigated by `make demo`).


---

## ADR-011 — Embed Customer 360 dependencies into the customers doc

**Context.** Reading a Customer 360 used to fan out: `customers` doc + three
$lookup stages (`transactions`, `quarantine_cases`, `features`) plus an
$unwind. P95 latency for the page was bounded by the slowest of those four
reads — and `transactions` grows unbounded, so the lookup degraded over time
even with a `(customer_id, timestamp DESC)` index.

**Decision.** Phase A migrates to a single document fetch. The `customers`
document now embeds the dependencies the page actually needs:

- `recent_transactions` (cap 50, newest-first; `$push` with `$position 0`,
  `$slice 50` from the change-stream tail in `feature_engineer`)
- `open_cases` (open / under_review only; `$push` from the case change-stream
  watcher, `$pull` on resolve, positional update on status flip)
- `latest_features` (snapshot of the freshest features doc; `$set` from the
  same change-stream tail)

The full transaction history, the resolved/dismissed cases, and the full
features collection remain in their own collections — they're just not
needed for the 360 read path. The 360 pipeline collapses to:

    [{$match: {customer_id: ...}}, {$project: {_id: 0}}]

Writes are mirrored idempotently from authoritative collections so source-of-
truth is unchanged. Backfill (`scripts.backfill_customer_embeds`) is
idempotent and bulk-writes 500 customers per batch.

**References.** ADR-001 (embed cohesive data), ADR-002 (PyMongo Async — no
$lookup orchestration glue), ADR-009 (no regex), ADR-010 (workers handle the
embed sync).

**Consequence.** 360 reads collapse from 4 round-trips of varying cost to a
single indexed point lookup. Trade-off: write amplification — every txn
insert and every case lifecycle event now also touches the `customers` doc
(2 writes instead of 1 per transaction; 1 extra write per case state
change). Acceptable because (a) the customers collection has a unique index
on `customer_id`, (b) the embeds are bounded (50 / SLA-bounded / 1), and (c)
the sync is fire-and-forget from the change-stream tail with structured
warning logs on failure rather than blocking the source-of-truth write.

## ADR-012 — Single `customers` collection with a `customer_type` discriminator

**Context.** The Acme reality has two materially different customer
populations: residential subscribers (one account per IC, 1–3 set-top boxes,
≤50 transactions/month) and commercial outlets (parent-account hierarchies,
5–20 devices per outlet, 100+ transactions per bill cycle). They share most
fields but their bounded-array footprints differ by an order of magnitude.

The Phase C plan splits them into two collections
(`customers_residential` + `customers_commercial`) plus a routing
`customer_index`. That split is invasive: every worker, every embed-sync
hook, every route, and the dashboard's "search across both" path has to
learn the two-collection model. Phase B is not paying that bill.

**Decision.** Until Phase C ships, model both populations in a single
`customers` collection using a `customer_type: "residential" | "commercial"`
discriminator and an optional `commercial: CommercialProfile` sub-document.
A Pydantic `model_validator` enforces `commercial` iff
`customer_type == COMMERCIAL`. The $jsonSchema validator extends
`CUSTOMER_VALIDATOR` with the discriminator enum and an optional
`commercial` object.

Routes stay single-pathed (`/api/customers/{id}`); the 360 view returns the
full doc and the frontend renders the commercial sub-tile only when the
discriminator says so. Workers do not branch on `customer_type` — the
embed-maintenance contract is identical for both populations.

**References.** ADR-006 (discriminator pattern), ADR-011 (single-doc 360
read).

**Consequence.** Residential and commercial coexist behind one indexed
collection without forcing the dashboard, search index, or
embed-maintenance workers to learn a two-collection world. The cost is the
50-element `recent_transactions` cap is too small for some commercial
outlets — the residential tail dominates the embed and busy outlets fall
back to the full `transactions` collection for their own history (which is
acceptable because the 360 page is operational, not financial reporting).

**Status.** Active in Phase B. Will be **superseded by ADR-015** if Phase
C.1 ships, since C.1 introduces typed collections + `customer_index` and
makes the discriminator redundant.

## ADR-013 — Acme-native rule pack expansion (Phase B.2)

**Context.** The Phase A rule corpus (`discount_mismatch`,
`velocity_anomaly`, `amount_outlier`, `entitlement_mismatch`,
`geographic_anomaly`, `duplicate_transaction`) is generic-fraud-shaped.
Acme's billing-revenue-assurance team cares about a different set of
patterns: post-cancellation termination fees, unearned/earned revenue
segregation, double charges across SKU codes, and pro-rata mid-cycle
upgrades. The Phase A pack does not surface those.

**Decision.** Add four Acme-native rule types (Phase B.2) into the
existing discriminated-union shape from ADR-006:

- `termination_fee_check` — termination fee within N days of cancellation;
  parameters: `lookback_days`, `termination_codes`.
- `unearned_earned_segregation` — recognised revenue without an active
  entitlement; parameters: `revenue_codes`, `grace_period_hours`.
- `double_charge_multi_code` — same package billed across two charge
  codes inside one cycle; parameters: `cycle_window_hours`, `code_pairs`.
- `proration_check` — non-prorated charge after a mid-cycle upgrade;
  parameters: `upgrade_codes`, `tolerance_pct`.

Each gets its own `*Params` Pydantic class wired into the discriminated
union, its own builder in `pipelines/rule_pipeline_builders.py` (equality
/ `$in` / date arithmetic only — no `$regex`, per ADR-009), and its own
matching `acme-rule-*` ASP processor name in `core.constants.ASP_PROCESSORS`.
The `RULE_VALIDATOR` enum is extended; the Phase B.2 ASP deployment file
ships one processor per new rule type.

**References.** ADR-006 (discriminated rule schema), ADR-007 (change-stream
activation), ADR-009 (no regex), ADR-010 (per-rule ASP processor).

**Consequence.** The dashboard's queue is now populated with cases the
Acme analyst team would actually triage, not generic anomaly noise.
Adding a fifth rule type is a four-touch change (enum + Params class +
builder + ASP processor name + seed). Each rule is independently
shadow-/active-toggleable via the existing `mode` field, so rolling out a
new pattern still goes through the standard shadow → active flow.

## ADR-014 — Standardized `ai_assist` contract on quarantine cases

**Context.** The original `QuarantineCase.ai_assist` was a free-form `dict`.
Routes returned whatever the upstream RAG pipeline emitted, the frontend
guessed at the shape, and the `$jsonSchema` validator could not enforce a
contract. Two failure modes recurred: the model occasionally returned
`likelihood: "unknown"` (not a real disposition), and the degraded fallback
produced empty `rationale` / `recommended_steps` arrays that broke the UI's
bulleted layout.

**Decision.** Introduce a typed `AiAssist` Pydantic model (Phase B.4) with
required fields: `summary`, `likelihood: AssistLikelihood`, `confidence`,
`rationale: list[str]` (`min_length=1`), `recommended_steps: list[str]`
(`min_length=1`), `references: list[AiAssistCitation]`, `retrieval:
AiAssistRetrieval`, `generated_at`, `model`, `degraded`, and
`degraded_reason`. `AssistLikelihood` is a `Literal["true_positive",
"false_positive", "needs_more_info"]` — three buckets that match the
analyst-disposition vocabulary.

The new `AiAssistService.generate(case_id, k, threshold, force)` is the
single producer:

- Idempotent — fresh assists (within `freshness_seconds`, default 300) are
  returned as-is with `cached: True`.
- Coercive — unknown likelihood values are mapped to `needs_more_info`
  before the model is constructed.
- Backfilling — empty `rationale` and `recommended_steps` from the
  degraded path are populated with sentinel strings so the validator
  never trips and the UI always has something to render.

The validator is extended in `setup_validators.py` to require the same
shape on persisted docs.

**References.** ADR-006 (validator-as-contract pattern), ADR-009
(no regex in the `references` filter path), ADR-011 (the projection lives
on `quarantine_cases`, not embedded — case docs are not in the 360 hot
path).

**Consequence.** The frontend can render the assist as a strongly-typed
component (no more conditional defaults for missing fields). The validator
catches drift the moment it's persisted; degraded responses still validate
because the service forces a minimum payload. Adding a future field
(e.g., `next_best_offer_id` after Phase B.6 wires NBO into RAG) is one
schema bump on both sides — frontend and backend share one contract.

---

## ADR-032 — Atlas Auto Embedding (`embed_source.text`) supersedes BYO Voyage

**Context.** The pre-2026-05-08 retrieval path was application-managed:
`EmbeddingService` called the Voyage SDK at write time
(`embed_customer`, `embed_text`, `embed_batch`), persisted a 1024-dim
`embedding: float[]` field on every history doc, persisted the source
text alongside in `embedding_text`, and re-embedded the same string at
query time so the `$vectorSearch` stage could pass `queryVector`. That
pattern bought us full control over the model and dims, and cost us
five things at once: (1) two source-of-truth surfaces — the corpus
text and the vector — to keep in sync; (2) a `voyageai>=0.3` runtime
dep, a `VOYAGE_API_KEY` secret, and a `voyage_model` /
`voyage_input_type` settings tax across `app/config.py` and
`backend/.env`; (3) a long-running `embedding_refresher` worker
plus a one-shot `reembed_history_corpus` script that only existed
to re-vectorise drift between code and corpus; (4) the
`backfill_customer_embeds` script that had to run any time the
embed text shape changed; (5) ADR-005's discipline of pinning
`input_type=document` everywhere — invisible to the validator,
trivial to break.

Atlas 8.0 introduced Auto Embedding (the `model` field on a
Vector Search index definition): give Atlas a string path
(`path: "embed_source.text"`), an embedding model
(`voyage-4-large`), and an `input_type`, and Atlas embeds at write
time *and* at query time, in-cluster, against the project-level
Voyage credential. The application sees only `embed_source.text`;
the vector lives entirely inside the index.

**Decision.** Migrate every Streaming Billing collection that participates
in vector retrieval to Atlas Auto Embedding behind the `AUTOEMBED`
feature flag. Concretely:

1. Replace `embedding`, `embedding_text`, `embedding_model`,
   `embedding_input_type`, and `embedding_generated_at` on
   `quarantine_cases_history` with a single required object
   `embed_source: { text: string, minLength: 1 }`. Bump the
   document to `_schema_version=4` (`SCHEMA_VERSION_V4`).
2. Replace the legacy `case_history_vector_idx` (path: `embedding`,
   numDimensions: 1024, `queryVector` form) with
   `case_history_autoembed_idx` (path: `embed_source.text`,
   `model: voyage-4-large`, `input_type: document`, `query` form).
   `setup_indexes.py` drops the legacy index, creates the AutoEmbed
   index, and polls `$listSearchIndexes` for `status` ∈
   `{READY, STEADY}` *and* `queryable: true` (timeout 600s,
   interval 2s) before returning. `teardown.py --full` drops both
   the new index and any leftover `case_history_vector_idx`.
3. `QuarantineService._archive_to_history` builds the canonical
   embed text via `build_history_embed_text(archived)` (the
   same helper the tests pin) and stamps it on
   `archived["embed_source"] = {"text": ...}`. No vector write.
4. `RagService.assist` passes raw text to the builder via
   `SearchPipelineBuilder.with_vector_search(query_text, ...)` which
   emits `query` (not `queryVector`) and defaults `path` to
   `embed_source.text`. Projections include `embed_source: 1`;
   the lean schema's `embed_source.text` powers the
   `_build_similar_preview` excerpt on `AiAssistService`.
5. Strip the application-side embedding plumbing entirely:
   - Delete `app/workers/embedding_refresher.py`,
     `scripts/reembed_history_corpus.py`, and
     `scripts/backfill_customer_embeds.py` plus their tests.
   - Reduce `EmbeddingService` to a static-method namespace
     containing only the deterministic text builders
     (`case_to_embedding_text`, `history_to_embedding_text`, and
     the small formatters they share). The `embed_*` methods,
     the `voyageai` import, and `__init__` are gone.
   - Remove the `voyageai>=0.3` dep from `backend/pyproject.toml`,
     the `voyage_*` fields from `app/config.py`, and the
     `VOYAGE_*` keys from `backend/.env`.
   - Drop the `EmbeddingService` constructor argument from
     `RagService`, `QuarantineService`, and the DI factories in
     `app/routes/quarantine.py`,
     `app/workers/case_lifecycle_worker.py`, and
     `app/workers/assist_agent_worker.py`.

The Voyage credential moves out of application config entirely:
it is registered once at the Atlas project level
(Project → Settings → Embedding Model Providers — see
`docs/atlas-autoembed-setup.md`) and Atlas uses it in-cluster.
The legacy BYO architecture is preserved verbatim in
`docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for
revert reference.

**References.** ADR-004 (`SearchPipelineBuilder` is the single
seam — `with_vector_search` is the only stage that changes shape),
ADR-005 (its `input_type=document` discipline now lives inside the
Atlas index definition rather than scattered across application
code — the constraint survives, the discipline doesn't), ADR-006
(validator-as-contract: the JSON Schema validator on
`quarantine_cases_history` enforces the `embed_source.text`
minimum-length floor at the database boundary), ADR-014 (the
`AiAssistRetrieval.embedding_model` field is now the literal
`"atlas-autoembed"` — one source of truth for the assist receipt).

**Consequence.** One vector source of truth (Atlas), no
`voyageai` runtime dep, no refresher / reembed / backfill workers,
no `VOYAGE_*` secret in app config, and a JSON Schema validator
that catches a missing `embed_source.text` the moment the doc is
persisted. Atlas owns the cost-and-latency budget for embedding —
seeding (`make seed-small`) becomes free at the application level
(no Voyage SDK calls during seed) and the Atlas index sync is the
new long-pole on first bring-up. Trade-off: the model and dim
choice now lives in the Atlas index definition, not in
`backend/.env`; switching models is a `setup_indexes.py` re-run
plus the `status: READY` wait. Forward-compatibility: future
collections that need vector retrieval (e.g. a `next_best_offer`
RAG corpus) follow the same shape — one indexed string path,
zero application embedding code.
