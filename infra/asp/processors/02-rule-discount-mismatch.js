// -----------------------------------------------------------------------------
// acme-rule-discount-mismatch — windowless
//
// V3 wire shape: fires when the event carries a `discounts[]` entry tagged
// with the simulator's PROMO_ANOMALY sentinel. This is the controlled signal
// the factory emits when the `discount` anomaly flavour is selected; real
// promotions (PROMO_RETENTION_WINBACK etc.) do not trip the rule.
//
// We deliberately avoid the legacy `$lookup customers` + active_promotions
// shape: the simulator's discount anomaly stamps PROMO_ANOMALY on the
// discounts array, which is a closed-form signal and cheaper than the lookup.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const PROMO_SENTINEL = "PROMO_ANOMALY";

const proc = {
  name: "acme-rule-discount-mismatch",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    {
      $match: {
        "discounts.promo_id": PROMO_SENTINEL
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-disc-", { $toString: "$transaction_id" }] },
        rule_type:  "discount_mismatch",
        severity:   "high",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "discount_mismatch",
          rule_name: "Discount with no active promotion",
          severity:  "high",
          evidence:  { discounts: "$discounts" }
        }]
      }
    },
    {
      $project: {
        case_id: 1, rule_type: 1, severity: 1, status: 1, created_at: 1,
        rules_triggered: 1,
        transaction_id: 1, customer_id: 1, merchant_id: 1,
        transaction_type: 1, timestamp: 1
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
