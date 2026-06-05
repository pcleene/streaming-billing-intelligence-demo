// -----------------------------------------------------------------------------
// acme-feature-rolling-writer — windowless per-customer last-txn snapshot
//
// On every event, $set the customer's rolling.last_txn_* fields in features.
// Heavier 24h/7d/30d windows are produced by the Spark Structured Streaming
// job (see ml/); this processor keeps the sub-minute fields fresh enough
// to power live dashboards.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-feature-rolling-writer",
  pipeline: [
    {
      $source: {
        connectionName: KAFKA_CONN,
        topic: TOPIC
      }
    },
    {
      $addFields: {
        customer_id: "$customer_id",
        "rolling.last_txn_at":     { $toDate: "$timestamp" },
        "rolling.last_txn_amount": "$amount",
        "rolling.last_txn_type":   "$transaction_type",
        updated_at:                { $toDate: "$_ts" }
      }
    },
    {
      $project: {
        customer_id: 1,
        rolling: 1,
        updated_at: 1
      }
    },
    {
      $merge: {
        into: { connectionName: ATLAS_CONN, db: DB, coll: "features" },
        on: "customer_id",
        whenMatched: "merge",
        whenNotMatched: "insert"
      }
    }
  ]
};

deployProcessor(proc);
