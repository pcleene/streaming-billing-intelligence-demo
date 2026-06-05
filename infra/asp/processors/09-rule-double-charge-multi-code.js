// -----------------------------------------------------------------------------
// acme-rule-double-charge-multi-code — windowless (single-event)
//
// V3 wire shape: fires when a single PPV transaction carries items[] with
// the SAME content_id under MULTIPLE distinct charge codes (e.g. one
// CC_PPV_DIRECT and one CC_BUNDLE_PPV). The simulator emits this shape
// only on the `double_ppv` anomaly flavour — normal ppv_charge events
// have exactly one item.
//
// Switched away from the 10-min tumblingWindow grouping because V3 PPV
// events never repeat the same content_id across separate transactions
// for the same customer in real distributions; the cross-charge-code
// double-bill is fundamentally a single-transaction shape.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const REDUNDANT_CODES = ["CC_PPV_DIRECT", "CC_BUNDLE_PPV"];

const proc = {
  name: "acme-rule-double-charge-multi-code",
  pipeline: [
    { $source: { connectionName: KAFKA_CONN, topic: TOPIC } },
    { $match: { transaction_type: "ppv_charge" } },
    {
      $addFields: {
        _ppv_items: {
          $filter: {
            input: { $ifNull: ["$items", []] },
            as:    "i",
            cond:  { $in: ["$$i.charge_code", REDUNDANT_CODES] }
          }
        }
      }
    },
    {
      $addFields: {
        _content_ids:  { $setUnion: ["$_ppv_items.content_id", []] },
        _charge_codes: { $setUnion: ["$_ppv_items.charge_code", []] }
      }
    },
    {
      $match: {
        $expr: {
          $and: [
            { $gte: [{ $size: "$_ppv_items" },    2] },
            { $eq:  [{ $size: "$_content_ids" },  1] },
            { $gte: [{ $size: "$_charge_codes" }, 2] }
          ]
        }
      }
    },
    {
      $addFields: {
        case_id:    { $concat: ["case-dblchg-", { $toString: "$transaction_id" }] },
        rule_type:  "double_charge_multi_code",
        severity:   "high",
        status:     "open",
        created_at: { $toDate: "$_ts" },
        rules_triggered: [{
          rule_type: "double_charge_multi_code",
          rule_name: "Double charge across redundant charge codes",
          severity:  "high",
          evidence:  {
            content_id:   { $arrayElemAt: ["$_content_ids", 0] },
            charge_codes: "$_charge_codes",
            item_count:   { $size: "$_ppv_items" }
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
