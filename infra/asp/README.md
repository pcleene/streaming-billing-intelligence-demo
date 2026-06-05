# Atlas Stream Processing — acme-billing processors

The streaming layer is split into one file per processor for reviewability.

```
infra/asp/
├── _common.js                       Shared constants + deployProcessor() helper
├── deploy_all.js                    Thin orchestrator — loads each processor file
├── stop_all.js                      Stops + drops every acme-* processor
└── processors/
    ├── 00-event-ingest.js           Kafka → transactions (passthrough)
    ├── 01-feature-rolling-writer.js Per-customer last-txn snapshot in features
    ├── 02-rule-discount-mismatch.js Discount with no active promotion
    ├── 03-rule-velocity-anomaly.js  > 5 txns / 5min same customer × merchant
    ├── 04-rule-entitlement-mismatch.js PPV without entitlement
    ├── 05-rule-geographic-anomaly.js Txn state ≠ home state
    ├── 06-rule-duplicate-transaction.js Dup amount within 60s
    ├── 07-rule-termination-fee-check.js  Termination fee on still-active customer
    ├── 08-rule-unearned-earned-segregation.js Earned/unearned split missing
    ├── 09-rule-double-charge-multi-code.js Same service via 2 charge codes
    └── 10-rule-proration-check.js    Mid-cycle proration drift > 5%
```

Each processor file is self-contained: it `load("infra/asp/_common.js")` for
shared constants/helpers and ends with a single `deployProcessor(proc)` call.

## Run all

```sh
mongosh "$ASP_URI" --file infra/asp/deploy_all.js
```

`$ASP_URI` is the workspace URI from Atlas → Stream Processing → connect.

## Run one

```sh
mongosh "$ASP_URI" --file infra/asp/processors/02-rule-discount-mismatch.js
```

Idempotent: each processor file stops + drops + recreates + starts.

## Stop everything

```sh
mongosh "$ASP_URI" --file infra/asp/stop_all.js
```

## Connection registry (preconfigured in the ASP workspace)

| Connection name         | Type    | Notes                                                   |
| ----------------------- | ------- | ------------------------------------------------------- |
| `UtilitymskKafkaConnection` | Kafka   | MSK broker w/ IAM (OAUTHBEARER) — same as FuelRetail-Demo     |
| `FuelRetail_cluster`         | Atlas   | Cluster-level connection; routes to `streaming_billing` DB  |

## Processor catalog

| Name                                  | Pillar | Live? | Notes                                                  |
| ------------------------------------- | ------ | ----- | ------------------------------------------------------ |
| `acme-event-ingest`                  | 1      | ✓     | Kafka → `transactions` (idempotent on `transaction_id`)|
| `acme-feature-rolling-writer`        | 4      | ✓     | Last-txn rolling fields per customer in `features`     |
| `acme-rule-discount-mismatch`        | 2      | ✓     | $lookup customer; emits if no active promotion         |
| `acme-rule-velocity-anomaly`         | 2      | ✓     | 5-min tumbling window, > 5 txns / (customer, merchant) |
| `acme-rule-entitlement-mismatch`     | 2      | ✓     | $lookup customer entitlements (PPV)                    |
| `acme-rule-geographic-anomaly`       | 2      | ✓     | $lookup customer.address.state                         |
| `acme-rule-duplicate-transaction`    | 2      | ✓     | 60-s tumbling window, dup_count > 1                    |
| `acme-rule-termination-fee-check`        | 2      | ✓     | Termination fee charged while customer active or in grace |
| `acme-rule-unearned-earned-segregation`  | 2      | ✓     | Subscription/addon charge missing earned/unearned split |
| `acme-rule-double-charge-multi-code`     | 2      | ✓     | 10-min window: same content via 2 redundant charge codes |
| `acme-rule-proration-check`              | 2      | ✓     | Mid-cycle proration drift > 5% from expected             |

`amount_outlier` is **batch-only** — its evaluation needs `$setWindowFields`
(per-customer historical mean/stddev) which isn't supported in ASP today.
The Rule Studio's "test against history" runs the same logic over the
`transactions` collection via `app.pipelines.rule_pipeline_builders`.

## Hot-reload contract

ASP processors don't watch `quarantine_rules` directly. When an analyst flips
a rule to `active` in the Rule Studio:

1. The `rule_change_watcher` worker picks up the change-stream event,
2. The dashboard receives an SSE `rule_change` event,
3. The operator re-runs `make asp-deploy` (or the single-processor target),
   which restarts the affected processor.

This deliberate operator-in-the-loop step is the demo's safety boundary; in
production the redeploy would be CI-driven from a commit on the rules
collection.
