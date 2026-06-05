"""End-to-end seed orchestrator.

Usage:
  python -m scripts.seed                       # default: 10k customers, ~30k txns, 500 history
  python -m scripts.seed --customers 2000      # quick demo
  python -m scripts.seed --target-txns 5000    # tighter txn corpus
  python -m scripts.seed --skip-history        # skip embedding cost

Sequence (order matters):
  1. validators
  2. customers (residential)
  3. transactions (power-law per-customer count → ~target-txns)
  4. enrich_customers_360 (fills cross-entity metrics + embeddings now
     that recent_transactions previews exist)
  5. rules
  6. history (RAG corpus, AutoEmbed)
  7. PR-14 supplemental seeds (commercial, cases, metrics, features,
     drift, cycles)
  8. indexes
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.services.embedding_service import EmbeddingService
from scripts.enrich_customers_360 import run_enrich
from scripts.seed_bill_cycles import main as seed_bill_cycles
from scripts.seed_customers import seed_customers
from scripts.seed_customers_commercial import main as seed_customers_commercial
from scripts.seed_feature_drift_metrics import main as seed_feature_drift_metrics
from scripts.seed_features import main as seed_features
from scripts.seed_history import seed_history
from scripts.seed_quarantine_cases import main as seed_quarantine_cases
from scripts.seed_rules import seed_rules
from scripts.seed_system_metrics import main as seed_system_metrics
from scripts.seed_transactions import seed_transactions
from scripts.setup_indexes import setup_all_indexes
from scripts.setup_validators import apply_validators

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed Streaming Billing demo data")
    p.add_argument("--customers", type=int, default=10_000)
    p.add_argument("--target-txns", type=int, default=30_000,
                   help="Approximate global transaction count (power-law per customer).")
    p.add_argument("--txn-alpha", type=float, default=1.5,
                   help="Pareto/Lomax shape — lower = heavier tail.")
    p.add_argument("--max-txns-per-customer", type=int, default=60,
                   help="Per-customer cap for the power-law tail.")
    p.add_argument("--history", type=int, default=500)
    p.add_argument("--skip-history", action="store_true")
    p.add_argument("--skip-enrich", action="store_true",
                   help="Skip the customer 360 enrichment pass (cross-entity metrics + embeddings).")
    p.add_argument("--enrich-batch-size", type=int, default=200)
    p.add_argument("--skip-indexes", action="store_true")
    p.add_argument("--skip-pr14-seeds", action="store_true",
                   help="Skip PR-14 supplemental seeds (commercial, cases, metrics, drift, features, cycles)")
    p.add_argument("--quarantine-cases", type=int, default=25)
    p.add_argument("--commercial-parents", type=int, default=5)
    p.add_argument("--feature-docs", type=int, default=250)
    p.add_argument("--burst-samples", type=int, default=60)
    p.add_argument("--steady-samples", type=int, default=40)
    return p.parse_args()


async def main() -> None:
    configure_logging()
    args = _parse_args()
    await connect_mongo()
    try:
        db = get_db()
        logger.info("seed_started", db=db.name, args=vars(args))

        await apply_validators(db)

        # Phase 2: customers — must exist before transactions can attach
        # to them (txn generator reads tier, address, subscriptions, etc.).
        await seed_customers(db, count=args.customers)

        # Phase 3: commercial customers — also part of the customer base
        # iterated by `seed_transactions._stream_customers`. Land them
        # before transactions so the txn corpus covers both segments.
        if not args.skip_pr14_seeds:
            await seed_customers_commercial(db, parent_count=args.commercial_parents)

        # Phase 4: transactions — power-law per-customer count targeting
        # `--target-txns` globally.
        await seed_transactions(
            db,
            target_total=args.target_txns,
            alpha=args.txn_alpha,
            max_per_customer=args.max_txns_per_customer,
        )

        # Phase 5: enrich customers — now that `recent_transactions`
        # previews are stamped on each customer, run the 360 enrichment
        # pass to fill cross-entity metrics + customer embeddings.
        if not args.skip_enrich:
            embedding_service = EmbeddingService()
            metrics_aggregator: Any = None
            try:
                from app.services.metrics_aggregator_service import (  # type: ignore
                    MetricsAggregatorService,
                )
                metrics_aggregator = MetricsAggregatorService(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("metrics_aggregator_unavailable", error=str(exc))
            await run_enrich(
                db=db,
                embedding_service=embedding_service,
                metrics_aggregator=metrics_aggregator,
                dry_run=False,
                batch_size=args.enrich_batch_size,
                limit=None,
                customer_type=None,
            )
        else:
            logger.info("enrich_skipped")

        # Phase 6: rules.
        await seed_rules(db)

        # Phase 7: history (RAG corpus — AutoEmbed source text).
        if not args.skip_history:
            await seed_history(db, count=args.history)
        else:
            logger.info("history_skipped")

        # Phase 8: remaining PR-14 supplemental seeds. Commercial parents
        # already landed in phase 3 above; the rest reference customers,
        # cases, and features in their own dependency order.
        if not args.skip_pr14_seeds:
            await seed_quarantine_cases(db, count=args.quarantine_cases)
            await seed_system_metrics(
                db,
                samples_burst=args.burst_samples,
                samples_steady=args.steady_samples,
            )
            await seed_features(db, count=args.feature_docs)
            await seed_feature_drift_metrics(db)
            await seed_bill_cycles(db)
        else:
            logger.info("pr14_seeds_skipped")

        # Phase 9: indexes (last — content is in place so build is fast).
        if not args.skip_indexes:
            await setup_all_indexes(db)
        else:
            logger.info("indexes_skipped")
        logger.info("seed_complete")
    finally:
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
