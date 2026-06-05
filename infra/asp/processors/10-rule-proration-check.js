// -----------------------------------------------------------------------------
// acme-rule-proration-check — windowless
//
// Mid-cycle subscription change (metadata.is_mid_cycle_change = true) whose
// proration_amount_myr deviates from expected_proration_myr by more than 5%.
//
// IMPORTANT: only fires when BOTH proration fields are present. Missing
// fields are a data-quality issue (handled by ops/CDC), not a quarantine
// case. Firing on absence inflates volume without surfacing real drift.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const APPLIES_TO    = ["subscription_charge"];
const TOLERANCE_PCT = 0.05;

const proc = {
  name: "acme-rule-proration-check",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    {
      $match: {
        transaction_type: { $in: APPLIES_TO },
        "metadata.is_mid_cycle_change": true
      }
    },
    {
      $addFields: {
        _actual:   { $ifNull: ["$metadata.proration_amount_myr",  null] },
        _expected: { $ifNull: ["$metadata.expected_proration_myr", null] }
      }
    },
    {
      $match: {
        $expr: {
          $and: [
            { $ne: ["$_actual",   null] },
            { $ne: ["$_expected", null] },
            { $gt: ["$_expected", 0] },
            {
              $gt: [
                { $divide: [
                  { $abs: { $subtract: ["$_actual", "$_expected"] }},
                  "$_expected"
                ]},
                TOLERANCE_PCT
              ]
            }
          ]
        }
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-proration-", { $toString: "$transaction_id" }] },
        rule_type:  "proration_check",
        severity:   "medium",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "proration_check",
          rule_name: "Proration mismatch on mid-cycle change",
          severity:  "medium",
          evidence:  {
            actual_proration_myr:   "$_actual",
            expected_proration_myr: "$_expected",
            tolerance_pct:          TOLERANCE_PCT
          }
        }]
      }
    },
    {
      $project: {
        case_id: 1, rule_type: 1, severity: 1, status: 1, created_at: 1,
        rules_triggered: 1,
        transaction_id: 1, customer_id: 1, amount: 1, transaction_type: 1, timestamp: 1
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
