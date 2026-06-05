<!-- Portfolio repository -->

> **Streaming Billing Intelligence** — portfolio demonstration.
> MSK/ASP pipeline with FastAPI, SvelteKit, and Atlas
>
> This is a sanitized public version of a real-world prototype. Client names,
> credentials, internal endpoints, and proprietary assets have been removed; all
> configuration is environment-driven (`.env.example`). Authored by
> [Paul Cleenewerck](https://github.com/pcleene).

---

# Streaming Billing — Quarantine Intelligence Demo

Production-grade demo for Acme Malaysia's analytics team. Real-time transaction
quarantine, rule administration, GenAI-assisted analyst workflow, and a
streaming feature store backed by **MongoDB Atlas**, **AWS MSK + Atlas Stream
Processing**, **Voyage AI** (embeddings), and **Anthropic Claude via Bedrock**.

> Synthetic data only. Acme package names are real but transaction events,
> customer profiles, and case history are entirely generated.

---

## Architecture at a glance

```
                            ┌────────────────┐
  CRM (batch JSON)  ─────►  │  Ingestion     │
  Subscription API  ─────►  │  + consolidator│  ────►  MongoDB Atlas
                            │   (FastAPI)    │            │
  Billing/PPV  ───► MSK ───►   Atlas Stream  │            │  Change Streams
                            │   Processing   │            ▼
                            └────────────────┘     Feature Store
                                    │              + Vector Search
                                    ▼              + Rule Engine
                              quarantine_cases  ◄──┘
                                    │
                                    ▼
                           Bedrock Claude (RAG)
                                    │
                                    ▼
                            SvelteKit dashboard
                                    ▲
                                    └── Spark Structured Streaming
                                        (MongoDB Spark Connector v10.x)
                                        → Delta Lake → IsolationForest
```

See `docs/architecture.md` for full diagram.

---

## Repository layout

```
acme-billing/
├── backend/              FastAPI service (PyMongo Async, layered)
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── deps.py
│   │   ├── core/         constants, errors, logging, metrics
│   │   ├── schemas/      Pydantic v2 models (discriminated unions)
│   │   ├── repositories/ one per collection
│   │   ├── pipelines/    composable aggregation builders
│   │   ├── services/     business logic / orchestration
│   │   ├── routes/       HTTP layer (thin)
│   │   ├── streaming/    MSK + ASP wiring
│   │   ├── llm/          Bedrock client + structured prompts
│   │   └── workers/      simulator, feature engineer, watchers
│   └── tests/
├── frontend/             SvelteKit + TS + Tailwind + shadcn-svelte
├── data-generator/       Synthetic Malaysian customers + cases
├── ml/                   Spark Streaming + sklearn training notebook
├── infra/
│   ├── atlas-setup.md    Indexes + Vector Search index defs
│   └── asp/              Atlas Stream Processing JS deploy scripts
├── docs/                 architecture, decisions, demo script
└── docker-compose.yml
```

---

## Prerequisites

- Python 3.11+ (`pyenv install 3.11.10` recommended)
- Node 20+ / npm 10+
- `mongosh` (for ASP deploy scripts)
- AWS CLI v2 with SSO configured (`aws sso login` against the same profile used by FuelRetail-Demo)
- Local MongoDB Atlas X.509 cert at the path set in `.env` (`TLS_CERT_PATH`)
- Network access to MSK on port 9098 (IAM auth)

---

## Quick start

```bash
cp .env.example .env       # fill in any blanks (e.g. VOYAGE_API_KEY)
make install               # creates .venv, installs backend + data-generator + frontend deps
make topic-create          # creates the acme-billing-events MSK topic (one-time)
make seed                  # 10k customers + 500 historical resolved cases → Atlas
make asp-deploy            # push ASP pipelines via mongosh
make demo                  # docker compose up: backend, frontend, simulator, watchers
```

Open <http://localhost:5173>.

---

## Demo flow (20-minute walkthrough)

1. **Customer 360** — search a customer, flip the CRM-lag toggle to expose the
   blind spot the analytics team faces today.
2. **Live Operations** — watch transactions stream in, quarantine cases pop, P99
   latency tile.
3. **Rule Studio** — author a 7th rule live, run it against the last 1000
   transactions in shadow mode, promote to active.
4. **Quarantine Queue → AI Analyst Assist** — open a case, see Claude's
   summary + similarity-cited references to historical resolutions.
5. **Feature Store + Model Lifecycle** — show the streaming feature pipeline
   tick into Delta in seconds; cite drift-triggered retraining.

Full script: `docs/demo_script.md`.

---

## Critical design decisions

See `docs/decisions.md` for ADRs covering:

- PyMongo `AsyncMongoClient` (not Motor)
- Voyage embedding `input_type` consistency for shared vector space
- Asymmetric `quarantine_rules` schema with discriminator
- ASP-watches-rules-collection pattern for instant rule activation
- Spark Structured Streaming (not batch read) for feature freshness story

---

## Recent changes

See [`docs/CHANGELOG.md`](docs/CHANGELOG.md) for the PR-9 through
PR-15 feature delivery summary.

---

## Reusing Fuel Retail-Demo infrastructure

This demo intentionally points at the same MongoDB Atlas cluster, MSK cluster,
and Bedrock account as the existing **FuelRetail-Demo** demo to avoid spinning up new
infrastructure. New isolation:

- New database: `streaming_billing` (separate from `FuelRetail_fraud`/`FuelRetail_screening`)
- New Kafka topic: `acme-billing-events`
- New ASP processors: prefixed `acme-*`

The ASP connection registry entries (`UtilitymskKafkaConnection`, `FuelRetail_cluster`)
are reused as-is. See `infra/asp/deploy_all.js`.
