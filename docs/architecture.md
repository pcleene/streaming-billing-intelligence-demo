# Architecture — Streaming Billing

## System overview

```mermaid
flowchart TB
    subgraph Sources["Source systems (synthetic)"]
        CRM[CRM batch loader<br/>JSON / Postgres]
        SUB[Subscription / entitlement<br/>REST]
        BILL[Billing system<br/>transaction events]
    end

    subgraph MSK["AWS MSK (IAM auth)"]
        TOPIC[(acme-billing-events)]
    end

    subgraph ASP["Atlas Stream Processing"]
        P0[event_ingest<br/>passthrough → events]
        P1[per-rule processors<br/>quarantine evaluation]
    end

    subgraph Atlas["MongoDB Atlas (streaming_billing DB)"]
        C_CUSTOMERS[(customers)]
        C_TXN[(transactions)]
        C_RULES[(quarantine_rules)]
        C_CASES[(quarantine_cases)]
        C_HISTORY[(quarantine_cases_history<br/>+ vector index)]
        C_FEATURES[(features)]
    end

    subgraph Backend["FastAPI backend (layered)"]
        ROUTES[routes<br/>HTTP layer]
        SERVICES[services<br/>orchestration]
        REPOS[repositories<br/>MongoDB I/O]
        BUILDERS[pipelines<br/>aggregation builders]
        LLM[llm/bedrock_client]
        SSE[SSE watchers]
    end

    subgraph Workers["Background processes"]
        SIM[transaction_simulator]
        FEAT[feature_engineer]
        RULEW[rule_change_watcher]
    end

    subgraph Frontend["SvelteKit dashboard"]
        OVERVIEW[Overview / Live Ops]
        C360[Customer 360]
        STUDIO[Rule Studio]
        QUEUE[Quarantine Queue + AI Assist]
        FSTORE[Feature Store + Model Lifecycle]
    end

    subgraph DBX["Spark / Databricks pattern"]
        SPARK[Structured Streaming<br/>MongoDB Spark Connector v10.x]
        DELTA[(Delta Lake)]
        ML[IsolationForest + MLflow]
    end

    CRM --> ROUTES
    SUB --> ROUTES
    BILL --> SIM
    SIM --> TOPIC
    TOPIC --> ASP
    P0 --> C_TXN
    P1 --> C_CASES
    P1 -.reads.-> C_RULES
    P1 -.reads.-> C_CUSTOMERS

    ROUTES --> SERVICES --> REPOS
    SERVICES --> BUILDERS
    SERVICES --> LLM
    REPOS <--> Atlas

    C_CASES -- change stream --> SSE
    C_RULES -- change stream --> RULEW
    C_FEATURES -- change stream --> SPARK
    SPARK --> DELTA --> ML

    Backend <--> Frontend
    LLM <-- vector retrieval --> C_HISTORY
    FEAT --> C_FEATURES
```

## Layering — backend

```mermaid
flowchart LR
    HTTP[HTTP request] --> ROUTE[routes/*.py]
    ROUTE --> SVC[services/*.py]
    SVC --> REPO[repositories/*.py]
    SVC --> PIPE[pipelines/*.py]
    REPO --> MONGO[(MongoDB)]
    PIPE -.builds.-> AGG[aggregation pipeline]
    AGG --> REPO
    SVC --> LLM[llm/bedrock_client]
    LLM --> BEDROCK[AWS Bedrock]
    SVC --> EMB[services/embedding_service]
    EMB --> VOYAGE[Voyage AI]
```

**Rules of the road:**

- Routes never call repositories or build pipelines.
- Services never call PyMongo or Bedrock SDKs directly.
- Pipelines never execute — they only construct stages.
- Workers never share the FastAPI event loop.

## Quarantine evaluation flow (Pillar 2)

```mermaid
sequenceDiagram
    participant Sim as Simulator
    participant MSK as MSK topic
    participant ASP as ASP processor
    participant Cust as customers
    participant Rules as quarantine_rules
    participant Cases as quarantine_cases
    participant CS as Change stream
    participant FE as FastAPI SSE
    participant UI as Dashboard

    Sim->>MSK: produce txn event
    MSK->>ASP: stream
    ASP->>Cust: $lookup customer context
    ASP->>Rules: $lookup active rules
    ASP->>Cases: $merge new case if rule fires
    Cases-->>CS: insert event
    CS->>FE: change stream
    FE->>UI: SSE push
```

## RAG analyst flow (Pillar 3)

```mermaid
sequenceDiagram
    participant UI
    participant Route as routes/analyst
    participant Svc as rag_service
    participant Emb as embedding_service
    participant Build as SearchPipelineBuilder
    participant Repo as case_history_repo
    participant LLM as bedrock_client

    UI->>Route: GET /analyst/case/:id/assist
    Route->>Svc: assist(case_id)
    Svc->>Emb: embed(case context, input_type=document)
    Emb-->>Svc: vector
    Svc->>Build: vector + filter + lookup + threshold
    Build-->>Svc: pipeline stages
    Svc->>Repo: aggregate(pipeline)
    Repo-->>Svc: top-k similar cases
    Svc->>LLM: structured prompt(case, history, rules)
    LLM-->>Svc: AnalystAssistOutput (Pydantic)
    Svc-->>Route: response
    Route-->>UI: AI summary + cited references
```

## Feature store + Spark Streaming (Pillar 4)

```mermaid
flowchart LR
    subgraph Online["Online serving"]
        ENG[feature_engineer worker]
        FCOLL[(features collection)]
        SVC[GET /features/:customer_id<br/>≤50ms]
    end

    subgraph Streaming["Streaming → Delta"]
        CS[Change stream]
        STR[spark.readStream.format mongodb]
        DELTA[(Delta Lake)]
        CKPT[checkpoint dir]
    end

    subgraph Batch["Batch training"]
        BATCH[spark.read.format delta]
        ML[sklearn IsolationForest]
        MLF[MLflow registry]
    end

    ENG --> FCOLL
    FCOLL --> SVC
    FCOLL --> CS --> STR --> DELTA
    STR -.- CKPT
    DELTA --> BATCH --> ML --> MLF
```

The streaming and batch paths are intentionally decoupled. Feature freshness
is measured in seconds; training cadence is independent (scheduled or
drift-triggered).

## Collection map

| Collection | Purpose | Key indexes |
|---|---|---|
| `customers` | Unified customer view (profile + active subs + active promotions + entitlements) | `customer_id` (unique); Atlas Search index on `name`, `ic_number`, `account_id` |
| `transactions` | Full transaction history (referenced from customers) | `customer_id + timestamp`, `transaction_id` (unique) |
| `quarantine_rules` | Asymmetric rule documents (polymorphic by `rule_type`) | `enabled + mode`, `rule_type`, `name` (unique) |
| `quarantine_cases` | Live cases pending or resolved | `created_at` desc, `customer_id`, `status + severity` |
| `quarantine_cases_history` | Resolved-cases corpus for RAG | `_id`; Atlas Vector Search index on `embedding` (1024-d, cosine); metadata fields `rule_types_triggered`, `disposition`, `customer_segment` |
| `features` | Engineered features for online serving + Spark streaming source | `customer_id` (unique); `updated_at` for streaming watermark |
| `crm_snapshots` | Simulates the Redshift batch refresh (CRM lag toggle reads from this) | `as_of` desc |

## Index definitions

See `infra/atlas-setup.md` for full Atlas Search and Atlas Vector Search index
JSON definitions.
