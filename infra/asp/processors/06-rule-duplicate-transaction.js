// -----------------------------------------------------------------------------
// acme-rule-duplicate-transaction — 60-second tumbling window
//
// Same (customer_id, merchant_id, line_total_myr) appearing 3+ times inside a
// 60-second event-time window → quarantine case. Grouping uses the V3 line
// total (items[0].line_total_myr) since V3 events don't carry a top-level
// `amount`. Threshold >2 keeps quarantine selectivity near ~2% of traffic —
// a single retry doesn't open a case; an actual triple-charge pattern does.
//
// Excludes `subscription_charge` because the monthly fee is a constant per
// customer — a high-TPS demo trivially clusters these into 3-of-a-kind
// groups that are NOT duplicates; they're periodic billings.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-rule-duplicate-transaction",
  pipeline: [
    {
      $source: {
        connectionName: KAFKA_CONN,
        topic: TOPIC,
        timeField: { $toDate: "$timestamp" }
      }
    },
    { $match: { transaction_type: { $ne: "subscription_charge" } } },
    {
      $addFields: {
        _line_total: { $ifNull: [{ $arrayElemAt: ["$items.line_total_myr", 0] }, 0] }
      }
    },
    {
      $tumblingWindow: {
        interval: { size: 1, unit: "minute" },
        pipeline: [
          {
            $group: {
              _id: {
                customer_id: "$customer_id",
                merchant_id: "$merchant_id",
                amount:      "$_line_total"
              },
              dup_count: { $sum: 1 },
              txn_ids:   { $push: "$transaction_id" },
              first_at:  { $min: "$timestamp" },
              last_at:   { $max: "$timestamp" }
            }
          },
          { $match: { dup_count: { $gt: 2 } } }
        ]
      }
    },
    {
      $addFields: {
        case_id:     { $concat: ["case-dup-",
                                 { $toString: "$_id.customer_id" }, "-",
                                 { $toString: { $toDate: "$_stream_meta.window.end" } }] },
        customer_id: "$_id.customer_id",
        merchant_id: "$_id.merchant_id",
        amount:      "$_id.amount",
        rule_type:   "duplicate_transaction",
        severity:    "medium",
        status:      "open",
        created_at:  { $toDate: "$_stream_meta.window.end" },
        rules_triggered: [{
          rule_type: "duplicate_transaction",
          rule_name: "Duplicate within 60 seconds",
          severity:  "medium",
          evidence:  {
            dup_count: "$dup_count",
            txn_ids:   "$txn_ids",
            first_at:  "$first_at",
            last_at:   "$last_at"
          }
        }]
      }
    },
    {
      $project: {
        case_id: 1, customer_id: 1, merchant_id: 1, amount: 1,
        rule_type: 1, severity: 1, status: 1, created_at: 1, rules_triggered: 1
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
