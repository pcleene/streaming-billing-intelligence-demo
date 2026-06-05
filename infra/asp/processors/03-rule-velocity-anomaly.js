// -----------------------------------------------------------------------------
// acme-rule-velocity-anomaly — 5-min tumbling window
//
// More than 50 transactions for the same (customer_id, merchant_id) within a
// 5-minute event-time window → quarantine case. Threshold raised from >12 to
// >50 to match simulator baseline: at 20 tps × 60 customers × 4 merchants,
// the average cell-volume per 5-min window is ~25; >50 catches only the
// natural-variation tail so velocity isn't drowning the case mix.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-rule-velocity-anomaly",
  pipeline: [
    {
      $source: {
        connectionName: KAFKA_CONN,
        topic: TOPIC,
        timeField: { $toDate: "$timestamp" },
        partitionIdleTimeout: { size: 5, unit: "second" }
      }
    },
    {
      $tumblingWindow: {
        interval: { size: 5, unit: "minute" },
        pipeline: [
          {
            $group: {
              _id: { customer_id: "$customer_id", merchant_id: "$merchant_id" },
              txn_count:    { $sum: 1 },
              first_at:     { $min: "$timestamp" },
              last_at:      { $max: "$timestamp" },
              txn_ids:      { $push: "$transaction_id" },
              total_amount: { $sum: "$amount" }
            }
          },
          { $match: { txn_count: { $gt: 50 } } }
        ]
      }
    },
    {
      $addFields: {
        case_id:     { $concat: ["case-velocity-",
                                 { $toString: "$_id.customer_id" }, "-",
                                 { $toString: "$_id.merchant_id" }, "-",
                                 { $toString: { $toDate: "$_stream_meta.window.end" } }] },
        customer_id: "$_id.customer_id",
        merchant_id: "$_id.merchant_id",
        rule_type:   "velocity_anomaly",
        severity:    "medium",
        status:      "open",
        created_at:  { $toDate: "$_stream_meta.window.end" },
        rules_triggered: [{
          rule_type: "velocity_anomaly",
          rule_name: "Velocity anomaly (customer × merchant)",
          severity:  "medium",
          evidence:  {
            txn_count:      "$txn_count",
            window_minutes: 5,
            first_at:       "$first_at",
            last_at:        "$last_at",
            total_amount:   "$total_amount",
            txn_ids:        "$txn_ids"
          }
        }]
      }
    },
    {
      $project: {
        case_id: 1, customer_id: 1, merchant_id: 1, rule_type: 1, severity: 1,
        status: 1, created_at: 1, rules_triggered: 1,
        txn_count: 1, total_amount: 1
      }
    },
    {
      $merge: {
        into: { connectionName: ATLAS_CONN, db: DB, coll: "quarantine_cases" },
        on: "case_id",
        whenMatched: "keepExisting",
        whenNotMatched: "insert"
      }
    }
  ]
};

deployProcessor(proc);
