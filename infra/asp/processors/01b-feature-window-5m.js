// -----------------------------------------------------------------------------
// acme-feature-window-5m — 5-minute hopping window per customer
//
// Replaces the change-stream + `_recent_window` buffer dance that used to
// live in `app.workers.feature_engineer`. ASP is the right tool for windowed
// aggregates: `$hoppingWindow` lets us emit `txn_count_5m` / `amount_sum_5m`
// / `discount_sum_5m` for every customer every 30 s, computed over the
// preceding 5 minutes of Kafka events. No state stored on the features doc,
// no per-event $push, no prune+recompute round-trip.
//
// Window:    5 minutes
// Hop:       30 seconds
// Boundary:  event time (`$timestamp`)
// Sink:      features.{customer_id} via $merge whenMatched=merge
//
// Behaviour note: $hoppingWindow only emits a row for customers who had
// at least one event in the closing window. A customer who was active in
// the prior window but has been silent for >5 min keeps their last
// non-zero `txn_count_5m` value until they transact again. Live readers
// should project `last_txn_at` alongside `txn_count_5m` if staleness
// matters; the value is "events in the last 5 min as of <last hop close
// after their most recent event>". A periodic janitor could zero stale
// rows but is not in scope here.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-feature-window-5m",
  pipeline: [
    {
      $source: {
        connectionName: KAFKA_CONN,
        topic: TOPIC,
        timeField: { $toDate: "$timestamp" },
        partitionIdleTimeout: { size: 5, unit: "second" }
      }
    },

    // --- 1. Hopping window: 5-minute window, 30-second hop. ---------------
    {
      $hoppingWindow: {
        interval: { size: 5, unit: "minute" },
        hopSize:  { size: 30, unit: "second" },
        pipeline: [
          {
            $group: {
              _id: "$customer_id",
              txn_count_5m:    { $sum: 1 },
              amount_sum_5m:   {
                $sum: { $ifNull: ["$total_myr", { $ifNull: ["$amount", 0] }] }
              },
              discount_sum_5m: {
                $sum: {
                  $ifNull: [
                    "$total_discount_myr",
                    { $ifNull: ["$discount_amount", 0] }
                  ]
                }
              }
            }
          }
        ]
      }
    },

    // --- 2. Project to features.{customer_id} canonical shape. ------------
    {
      $project: {
        _id: 0,
        customer_id:     "$_id",
        txn_count_5m:    1,
        amount_sum_5m:   { $round: ["$amount_sum_5m",   2] },
        discount_sum_5m: { $round: ["$discount_sum_5m", 2] },
        updated_at:      { $toDate: "$_stream_meta.window.end" }
      }
    },

    // --- 3. Upsert by customer_id. ----------------------------------------
    // `whenMatched: merge` keeps the slow-path FE-worker fields (lineage,
    // counters, computed_signals pull-through) intact — ASP only writes
    // the four 5m-window fields + updated_at.
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
