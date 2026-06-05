// -----------------------------------------------------------------------------
// acme-rule-geographic-anomaly — windowless, $lookup customer.address.state
//
// Transaction location.state ≠ customer's home state → low-severity case.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-rule-geographic-anomaly",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    { $match: { "location.state": { $exists: true, $ne: null } } },
    {
      $lookup: {
        from: { connectionName: ATLAS_CONN, db: DB, coll: "customers" },
        localField: "customer_id",
        foreignField: "customer_id",
        as: "customer"
      }
    },
    { $unwind: "$customer" },
    { $match: { $expr: { $ne: ["$location.state", "$customer.address.state"] } } },
    {
      $addFields: {
        case_id:    { $concat: ["case-geo-", { $toString: "$transaction_id" }] },
        rule_type:  "geographic_anomaly",
        severity:   "low",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "geographic_anomaly",
          rule_name: "Geographic anomaly vs home state",
          severity:  "low",
          evidence:  {
            txn_state:  "$location.state",
            home_state: "$customer.address.state"
          }
        }],
        customer_snapshot: {
          customer_id: "$customer.customer_id",
          segment:     "$customer.segment",
          home_state:  "$customer.address.state"
        }
      }
    },
    {
      $project: {
        case_id: 1, customer_id: 1, transaction_id: 1, amount: 1,
        rule_type: 1, severity: 1, status: 1, created_at: 1,
        rules_triggered: 1, customer_snapshot: 1, location: 1
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
