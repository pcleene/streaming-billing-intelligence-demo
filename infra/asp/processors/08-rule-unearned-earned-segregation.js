// -----------------------------------------------------------------------------
// acme-rule-unearned-earned-segregation — windowless
//
// Subscription / addon charge whose earned_amount_myr + unearned_amount_myr
// split doesn't sum to the gross amount within tolerance_myr.
//
// IMPORTANT: only fires when BOTH split fields are present. Missing fields
// are a data-quality issue (handled by ops/CDC), not a quarantine case.
// Firing on absence produced ~94% of all cases in earlier tests.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const APPLIES_TO    = ["subscription_charge", "addon_purchase"];
const TOLERANCE_MYR = 0.5;

const proc = {
  name: "acme-rule-unearned-earned-segregation",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    { $match: { transaction_type: { $in: APPLIES_TO } } },
    {
      $addFields: {
        _earned:   { $ifNull: ["$metadata.earned_amount_myr",   null] },
        _unearned: { $ifNull: ["$metadata.unearned_amount_myr", null] },
        // V3 wire shape has no top-level `amount` — derive from items[].
        _gross: {
          $round: [{
            $sum: {
              $map: {
                input: { $ifNull: ["$items", []] },
                as: "i",
                in: { $ifNull: ["$$i.line_total_myr", 0] }
              }
            }
          }, 2]
        }
      }
    },
    {
      $match: {
        $expr: {
          $and: [
            { $ne: ["$_earned",   null] },
            { $ne: ["$_unearned", null] },
            {
              $gt: [
                { $abs: { $subtract: [
                  { $add: ["$_earned", "$_unearned"] },
                  "$_gross"
                ]}},
                TOLERANCE_MYR
              ]
            }
          ]
        }
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-uneseg-", { $toString: "$transaction_id" }] },
        rule_type:  "unearned_earned_segregation",
        severity:   "medium",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "unearned_earned_segregation",
          rule_name: "Earned/unearned split missing or out of tolerance",
          severity:  "medium",
          evidence:  {
            amount:              "$_gross",
            earned_amount_myr:   "$_earned",
            unearned_amount_myr: "$_unearned",
            tolerance_myr:       TOLERANCE_MYR
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
