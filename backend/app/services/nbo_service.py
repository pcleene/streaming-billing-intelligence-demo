"""Next-best-offer service (Phase B.6).

Single-document compute: reads the embedded fields on the customer doc
(latest_features, subscriptions, active_promotions, open_cases,
recent_transactions) and produces a `CustomerRecommendations`
projection. No joins, no $lookup — that's the whole point of ADR-011.

The result is persisted via `$set recommendations` so subsequent reads
of `/api/customers/{id}` get it for free.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import CustomerNotFound
from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository
from app.schemas.customer import (
    ChurnRisk,
    CustomerRecommendations,
    NextBestOffer,
)

logger = get_logger(__name__)


# Heuristic weights — tuned for the demo, not production.
_HIGH_RISK = 0.65
_MED_RISK  = 0.35


class NboService:
    def __init__(self, repo: CustomerRepository) -> None:
        self._repo = repo

    async def compute(self, customer_id: str) -> CustomerRecommendations:
        # PR-2: dispatch via the type-aware repo (which honours
        # `STORAGE_SPLIT` + `customer_index`).
        cust = await self._repo.get_by_customer_id(customer_id)
        if not cust:
            raise CustomerNotFound(customer_id)

        churn = self._score_churn(cust)
        offers = self._build_offers(cust, churn)
        recs = CustomerRecommendations(
            churn_risk=churn,
            next_best_offers=offers,
        )

        # Update goes back to whichever typed collection holds the doc.
        ctype = cust.get("customer_type")
        coll = self._repo._coll_for(ctype)
        await coll.update_one(
            {"customer_id": customer_id},
            {"$set": {"recommendations": recs.model_dump(mode="json")}},
        )
        return recs

    # ----- heuristics --------------------------------------------------

    def _score_churn(self, cust: dict[str, Any]) -> ChurnRisk:
        drivers: list[str] = []
        score = 0.0
        feats = cust.get("latest_features") or {}
        open_cases = cust.get("open_cases") or []
        subs = cust.get("subscriptions") or []
        recent = cust.get("recent_transactions") or []

        # Inactivity signal — long since last txn.
        m_since = feats.get("minutes_since_last_txn")
        if isinstance(m_since, (int, float)):
            if m_since > 60 * 24 * 14:  # > 14 days
                score += 0.40
                drivers.append("no_txn_in_14d")
            elif m_since > 60 * 24 * 7:
                score += 0.20
                drivers.append("no_txn_in_7d")

        # Spend-to-package ratio — paying for more than they use.
        ratio = feats.get("spend_to_package_ratio")
        if isinstance(ratio, (int, float)) and 0 < ratio < 0.30:
            score += 0.15
            drivers.append("low_package_utilisation")

        # Quarantine count drag — friction in past 30d.
        q30 = feats.get("quarantine_count_30d")
        if isinstance(q30, (int, float)) and q30 >= 3:
            score += 0.15
            drivers.append("recent_quarantines")

        # Open cases dragging — service issues unresolved.
        if len(open_cases) >= 2:
            score += 0.10
            drivers.append("multiple_open_cases")

        # Cancelled / paused subs in profile.
        non_active = sum(
            1 for s in subs if s.get("status") in ("cancelled", "paused")
        )
        if non_active >= 1:
            score += 0.10
            drivers.append("inactive_subscription")

        # Heavy discount usage.
        disc_rate = feats.get("discount_rate_30d")
        if isinstance(disc_rate, (int, float)) and disc_rate > 0.25:
            score += 0.05
            drivers.append("high_discount_dependency")

        # If we have effectively no signal, fall back to txn cadence.
        if not drivers and not recent:
            score = 0.30
            drivers.append("no_recent_activity")

        score = max(0.0, min(1.0, round(score, 3)))
        if score >= _HIGH_RISK:
            band = "high"
        elif score >= _MED_RISK:
            band = "medium"
        else:
            band = "low"
        return ChurnRisk(band=band, score=score, drivers=drivers)

    def _build_offers(
        self,
        cust: dict[str, Any],
        churn: ChurnRisk,
    ) -> list[NextBestOffer]:
        offers: list[NextBestOffer] = []
        feats = cust.get("latest_features") or {}
        subs = cust.get("subscriptions") or []
        active_promos = cust.get("active_promotions") or []
        package_value = float(feats.get("package_value_myr") or 0.0)
        spend_24h = float(feats.get("spend_24h_myr") or 0.0)

        # 1) High-risk → retention discount.
        if churn.band == "high":
            offers.append(NextBestOffer(
                offer_id="retention_15pct_3m",
                offer_type="retention_discount",
                title="15% off the next 3 bill cycles",
                rationale=(
                    "Churn risk is high "
                    f"({', '.join(churn.drivers[:2]) or 'inactivity'}); "
                    "lock in retention before next billing."
                ),
                expected_uplift_myr=round(package_value * 0.85 * 3, 2),
                priority=1,
            ))

        # 2) Heavy spender on a small package → upgrade.
        ratio = feats.get("spend_to_package_ratio")
        if isinstance(ratio, (int, float)) and ratio > 0.85 and package_value > 0:
            offers.append(NextBestOffer(
                offer_id="upgrade_next_tier",
                offer_type="upgrade",
                title="Upgrade to next package tier",
                rationale=(
                    "Spend-to-package ratio is "
                    f"{ratio:.0%} — customer would benefit from a larger plan."
                ),
                expected_uplift_myr=round(package_value * 0.30, 2),
                priority=2 if churn.band != "high" else 3,
            ))

        # 3) PPV-heavy in last 24h → addon bundle.
        ppv_recent = sum(
            1 for t in (cust.get("recent_transactions") or [])
            if t.get("transaction_type") == "ppv_purchase"
        )
        if ppv_recent >= 3:
            offers.append(NextBestOffer(
                offer_id="addon_ppv_bundle",
                offer_type="addon",
                title="Add the PPV bundle (save MYR 15/mo)",
                rationale=(
                    f"{ppv_recent} pay-per-view purchases in recent history — "
                    "a bundle is cheaper at this rate."
                ),
                expected_uplift_myr=15.0,
                priority=3,
            ))

        # 4) No active subs → winback.
        active = [s for s in subs if s.get("status") == "active"]
        if not active:
            offers.append(NextBestOffer(
                offer_id="winback_basic_3mo",
                offer_type="winback",
                title="Come back: 3 months on us",
                rationale="No active subscriptions — winback campaign target.",
                expected_uplift_myr=0.0,
                priority=4,
            ))

        # 5) Low-risk loyal high spender, no active promo → loyalty perk.
        if (
            churn.band == "low"
            and spend_24h > 100
            and not active_promos
        ):
            offers.append(NextBestOffer(
                offer_id="loyalty_perk_voucher",
                offer_type="loyalty_perk",
                title="MYR 25 loyalty voucher",
                rationale="Loyal high-spender with no active promo — reward to entrench.",
                expected_uplift_myr=25.0,
                priority=5,
            ))

        # Sort by priority asc (1 = highest).
        offers.sort(key=lambda o: o.priority)
        return offers
