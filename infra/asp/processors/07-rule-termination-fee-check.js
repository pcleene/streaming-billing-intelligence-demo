// -----------------------------------------------------------------------------
// acme-rule-termination-fee-check — windowless
//
// V3 wire shape: fires on transactions of type `termination_fee` whose
// billed amount drifts from the customer's `computed_expected_fee_myr`
// by more than DELTA_PCT.
//
// IMPORTANT: only fires when both `unit_price_myr` and
// `computed_expected_fee_myr` are present on the termination item AND the
// drift exceeds the threshold. Normal simulator emissions stay within ±5%
// drift; only the `termination_overcharge` anomaly flavour pushes beyond
// the threshold. Threshold tuned so this rule contributes a modest, not
// dominant, slice of cases.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const DELTA_PCT = 0.15;

const proc = {
  name: "acme-rule-termination-fee-check",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    { $match: { transaction_type: "termination_fee" } },
    {
      $addFields: {
        _item:     { $arrayElemAt: ["$items", 0] }
      }
    },
    {
      $addFields: {
        _actual:   { $ifNull: ["$_item.unit_price_myr",            null] },
        _expected: { $ifNull: ["$_item.computed_expected_fee_myr", null] }
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
                  { $abs: { $subtract: ["$_actual", "$_expected"] } },
                  "$_expected"
                ]},
                DELTA_PCT
              ]
            }
          ]
        }
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-termfee-", { $toString: "$transaction_id" }] },
        rule_type:  "termination_fee_check",
        severity:   "high",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "termination_fee_check",
          rule_name: "Termination fee drift vs computed expected",
          severity:  "high",
          evidence:  {
            actual_myr:    "$_actual",
            expected_myr:  "$_expected",
            delta_pct:     DELTA_PCT
          }
        }]
      }
    },
    {
      $project: {
        case_id: 1, rule_type: 1, severity: 1, status: 1, created_at: 1,
        rules_triggered: 1,
        transaction_id: 1, customer_id: 1, transaction_type: 1, timestamp: 1
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
