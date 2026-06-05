// -----------------------------------------------------------------------------
// acme-event-ingest — Kafka → transactions, with synchronous enrichment
//
// The simulator emits a thin V3 "wire shape" (transaction_id, customer_id,
// timestamp, items[], discounts, tax, payment, location, …). This processor
// $lookup-s the live customer doc, materialises the frozen `customer_summary`,
// recomputes totals from items[], and stamps the 6 STATELESS fields of
// `computed_signals` so downstream rule processors and analyst-facing reads
// see the same shape that seed_transactions writes.
//
// The 2 ROLLING signals (`txn_velocity_5m`, `amount_vs_avg_30d_pct`) are
// stamped slow-path by `app.workers.feature_engineer` via the transactions
// change-stream — they need windowed state ASP doesn't carry.
//
// Idempotent on `transaction_id`. Backs the dashboard live event feed and
// feeds downstream rule processors via the transactions collection.
// -----------------------------------------------------------------------------
load("infra/asp/_common.js");

const proc = {
  name: "acme-event-ingest",
  pipeline: [
    {
      $source: {
        connectionName: KAFKA_CONN,
        topic: TOPIC,
        timeField: { $toDate: "$timestamp" },
        partitionIdleTimeout: { size: 5, unit: "second" }
      }
    },

    // --- 1. Look up the live customer doc once. ----------------------------
    {
      $lookup: {
        from: { connectionName: ATLAS_CONN, db: DB, coll: "customers" },
        localField: "customer_id",
        foreignField: "customer_id",
        as: "_customer"
      }
    },
    { $unwind: { path: "$_customer", preserveNullAndEmptyArrays: true } },

    // --- 2. Recompute totals from items[]. ---------------------------------
    // Producer sends `unit_price_myr`, `discount_per_unit_myr`, `quantity`
    // per line; we re-derive subtotal/discount/total here so every txn doc
    // has consistent totals regardless of who wrote it.
    {
      $addFields: {
        subtotal_myr: {
          $round: [{
            $sum: {
              $map: {
                input: { $ifNull: ["$items", []] },
                as: "i",
                in: {
                  $multiply: [
                    { $ifNull: ["$$i.unit_price_myr", 0] },
                    { $ifNull: ["$$i.quantity", 1] }
                  ]
                }
              }
            }
          }, 2]
        },
        total_discount_myr: {
          $round: [{
            $sum: {
              $map: {
                input: { $ifNull: ["$items", []] },
                as: "i",
                in: {
                  $multiply: [
                    { $ifNull: ["$$i.discount_per_unit_myr", 0] },
                    { $ifNull: ["$$i.quantity", 1] }
                  ]
                }
              }
            }
          }, 2]
        },

        // --- 3. Frozen customer snapshot (denormalised at billing time). --
        customer_summary: {
          customer_id:   "$_customer.customer_id",
          name:          "$_customer.name",
          tier:          "$_customer.tier",
          segment:       "$_customer.segment",
          customer_type: "$_customer.customer_type",
          ic_number:     "$_customer.ic_number",
          account_id:    "$_customer.account_id",
          home_state:    "$_customer.address.state",
          active_promotions: { $ifNull: ["$_customer.active_promotions", []] },
          subscriptions: {
            $map: {
              input: { $ifNull: ["$_customer.subscriptions", []] },
              as: "s",
              in: {
                package_code:    "$$s.package_code",
                package_name:    "$$s.package_name",
                status:          "$$s.status",
                monthly_fee_myr: "$$s.monthly_fee_myr"
              }
            }
          }
        }
      }
    },

    // --- 4. Tax + total derive from subtotal/discount (second pass). ------
    {
      $addFields: {
        tax: {
          rate: 0.06,
          name: "SST",
          amount_myr: {
            $round: [{
              $multiply: [
                {
                  $max: [
                    0,
                    { $subtract: ["$subtotal_myr", "$total_discount_myr"] }
                  ]
                },
                0.06
              ]
            }, 2]
          }
        }
      }
    },
    {
      $addFields: {
        total_myr: {
          $round: [{
            $add: [
              { $subtract: ["$subtotal_myr", "$total_discount_myr"] },
              "$tax.amount_myr"
            ]
          }, 2]
        },

        // --- 5. The 6 STATELESS computed_signals. --------------------------
        // Pure arithmetic + customer-doc lookups; no rolling window state.
        // The rolling pair (txn_velocity_5m, amount_vs_avg_30d_pct) is filled
        // slow-path by feature_engineer.
        computed_signals: {
          discount_pct_of_subtotal: {
            $cond: [
              { $gt: ["$subtotal_myr", 0] },
              { $round: [{ $divide: ["$total_discount_myr", "$subtotal_myr"] }, 4] },
              0
            ]
          },

          is_promo_active: {
            $gt: [
              { $size: { $ifNull: ["$_customer.active_promotions", []] } },
              0
            ]
          },

          // Approximation: any active subscription with a future
          // `lock_in_end_at`. Robust to the field being missing.
          is_lock_in_period: {
            $gt: [
              {
                $size: {
                  $filter: {
                    input: { $ifNull: ["$_customer.subscriptions", []] },
                    as: "s",
                    cond: {
                      $and: [
                        { $eq: ["$$s.status", "active"] },
                        {
                          $gt: [
                            { $toDate: { $ifNull: ["$$s.lock_in_end_at", "1970-01-01T00:00:00Z"] } },
                            { $toDate: "$_ts" }
                          ]
                        }
                      ]
                    }
                  }
                }
              },
              0
            ]
          },

          // Demo proxy: a ppv_charge from a customer with no entitlements
          // is treated as a likely first-time PPV. Strict accuracy would
          // require scanning prior transactions which doesn't belong in
          // a stateless ASP stage.
          is_first_ppv: {
            $and: [
              { $eq: ["$transaction_type", "ppv_charge"] },
              {
                $eq: [
                  { $size: { $ifNull: ["$_customer.entitlements", []] } },
                  0
                ]
              }
            ]
          },

          // Demo geographic distance: 0 if same state as customer's home,
          // else a fixed cross-state proxy. A real implementation would do
          // Haversine over `customer.address.geo` lat/lng; demo customers
          // don't carry coordinates so we use a state-equality flag.
          geographic_distance_from_home_km: {
            $cond: [
              { $eq: ["$location.state", "$_customer.address.state"] },
              0,
              1500
            ]
          },

          // Only meaningful on termination_fee txns; null otherwise so the
          // dashboard doesn't show 0 for unrelated txn types.
          termination_fee_pct_of_expected: {
            $cond: [
              {
                $and: [
                  { $eq: ["$transaction_type", "termination_fee"] },
                  { $gt: [{ $ifNull: ["$_customer.early_termination_fee_myr", 0] }, 0] }
                ]
              },
              {
                $round: [{
                  $divide: [
                    { $ifNull: ["$total_myr", 0] },
                    "$_customer.early_termination_fee_myr"
                  ]
                }, 4]
              },
              null
            ]
          }
        }
      }
    },

    // --- 6. Promote validator-required top-level fields from the customer
    //         lookup + derive `cycle_id` from the event timestamp.
    //         Validator (setup_validators.py) requires top-level
    //         `customer_type`, `account_id`, `cycle_id`, plus integer
    //         `_schema_version`. seed_transactions.py writes the same shape.
    {
      $addFields: {
        customer_type: "$_customer.customer_type",
        account_id:    "$_customer.account_id",
        cycle_id: {
          $concat: [
            "CYC_",
            { $dateToString: { format: "%Y%m", date: { $toDate: "$timestamp" } } },
            "_",
            "$customer_id"
          ]
        }
      }
    },

    // --- 7. Stream metadata + drop the lookup helper. ----------------------
    {
      $addFields: {
        _ingested_at:  { $toDate: "$_ts" },
        _source_topic: TOPIC,
        _schema_version: 3
      }
    },
    { $unset: "_customer" },

    // --- 7. Upsert by transaction_id. --------------------------------------
    // `whenMatched: merge` lets the slow-path feature_engineer add
    // `computed_signals.txn_velocity_5m` / `.amount_vs_avg_30d_pct` later
    // without clobbering the ingest-time fields.
    {
      $merge: {
        into: { connectionName: ATLAS_CONN, db: DB, coll: "transactions" },
        on: "transaction_id",
        whenMatched: "merge",
        whenNotMatched: "insert"
      }
    }
  ]
};

deployProcessor(proc);
