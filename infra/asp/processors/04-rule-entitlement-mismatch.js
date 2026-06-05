// -----------------------------------------------------------------------------
// acme-rule-entitlement-mismatch — windowless
//
// V3 wire shape: PPV charge (`transaction_type=ppv_charge`) whose
// items[0].content_id is not in the customer's entitlements list →
// quarantine case.
//
// IMPORTANT: only fires when a content_id IS present on the line item.
// Non-PPV transactions and PPV without content_id are skipped (no signal,
// no false positives). The factory normally picks content_id from the
// customer's own entitlements, so this rule fires only on the
// `entitlement` anomaly flavour (PPV_HIDDEN_LEAK_*).
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-rule-entitlement-mismatch",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    {
      $match: {
        transaction_type: "ppv_charge",
        "items.content_id": { $exists: true, $ne: null }
      }
    },
    {
      $addFields: {
        _content_id: { $arrayElemAt: ["$items.content_id", 0] }
      }
    },
    {
      $lookup: {
        from: { connectionName: ATLAS_CONN, db: DB, coll: "customers" },
        localField: "customer_id",
        foreignField: "customer_id",
        as: "customer"
      }
    },
    { $unwind: "$customer" },
    {
      $match: {
        $expr: {
          $not: {
            $in: [
              "$_content_id",
              { $ifNull: ["$customer.entitlements.content_id", []] }
            ]
          }
        }
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-ent-", { $toString: "$transaction_id" }] },
        rule_type:  "entitlement_mismatch",
        severity:   "high",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "entitlement_mismatch",
          rule_name: "PPV without entitlement",
          severity:  "high",
          evidence:  { content_id: "$_content_id" }
        }],
        customer_snapshot: {
          customer_id: "$customer.customer_id"
        }
      }
    },
    {
      $project: {
        case_id: 1, customer_id: 1, transaction_id: 1, transaction_type: 1,
        rule_type: 1, severity: 1, status: 1, created_at: 1,
        rules_triggered: 1, customer_snapshot: 1
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
