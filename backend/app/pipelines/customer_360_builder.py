"""Pipeline builder for the Customer 360 view.

Phase A migration (ADR-011) + PR-2 (ADR-020/021): the 360 read is a
single document fetch — every dependency the page renders is now
embedded on the customer doc:
  - recent_transactions  (≤50, newest-first; maintained by the change-stream
                          tail in feature_engineer)
  - open_cases           (open / under_review only; maintained by
                          rule_change_watcher and the disposition service)
  - latest_features      (snapshot of the freshest features doc; maintained
                          by feature_engineer)

The pipeline stays a list of stages (rather than a single `find_one`) so the
service layer can compose additional projection stages without changing its
API. Caller selects the collection via `customer_index` (when
`FeatureFlags.STORAGE_SPLIT` is on) or against the legacy `customers`
collection.
"""

from __future__ import annotations


def customer_360_pipeline(customer_id: str) -> list[dict]:
    """Single-stage `$match` + project. EXPLAIN should show `IXSCAN` on
    `customer_id` and no `LOOKUP` stage."""
    return [
        {"$match": {"customer_id": customer_id}},
        {"$project": {"_id": 0}},
    ]
