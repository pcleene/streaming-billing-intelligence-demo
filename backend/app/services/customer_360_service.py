"""Customer 360 composition service (PR-9).

Reads the V3 customer document as-is from `CustomerRepository` (no joins —
the doc already carries every field the dashboard needs after PR-9
enrichment) and shapes it for the API. Two response shapes:

- *rich* (RICH_CUSTOMER_360 = True): the full V3 profile with
  identity scalars, contact, address, entities, entity_profiles,
  cross_entity_metrics, brand_journey, interaction_history,
  active_campaigns, recommendations, equipment, current_cycle.
- *lean*: the legacy envelope used by the existing frontend.

This is parallel to (not a replacement for) `CustomerService.get_360`.
The lean path on `CustomerService.get_360` stays canonical for the
legacy `/api/customers/{id}` route during the rollout — `Customer360Service`
is wired into the new `/api/customers/{id}/profile` route so flips of
`flags.RICH_CUSTOMER_360` don't disturb the legacy callers.
"""

from __future__ import annotations

from app.core.errors import CustomerNotFound
from app.core.feature_flags import flags
from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository

logger = get_logger(__name__)


# Top-level keys that make up the V3 rich profile. Anything not in this set
# (plus the lean keys below) is dropped from the rich response so the wire
# shape is stable regardless of what miscellaneous fields the enrichment
# worker has tacked onto the document.
_RICH_KEYS: tuple[str, ...] = (
    "customer_id",
    "customer_type",
    "account_id",
    "parent_account_id",
    "outlet_id",
    "tier",
    # Identity scalars (lifted to root in V3)
    "name",
    "preferred_name",
    "ic_number",
    "date_of_birth",
    "ethnicity",
    "gender",
    "marital_status",
    "household_size",
    "occupation_band",
    # Embedded identity blocks (root-level)
    "contact",
    "address",
    # Commercial-only profile block
    "business_profile",
    # Cross-entity & history
    "entities",
    "entity_profiles",
    "cross_entity_metrics",
    "brand_journey",
    "interaction_history",
    "active_campaigns",
    "recommendations",
    "equipment",
    "current_cycle",
    # Operational hot-path embeds — surfaced so the rich profile page can
    # render the full picture without a second roundtrip. (These already
    # live on the document.)
    "subscriptions",
    "active_promotions",
    "entitlements",
    "recent_transactions",
    "recent_support",
    "open_cases",
    "latest_features",
    "total_monthly_value_myr",
    "lifetime_quarantine_count",
    # AutoEmbed source — the rich profile UI surfaces the indexed
    # `embed_source.text` so analysts can see what string the customer
    # search semantic index sees. Atlas-managed vectors are NEVER
    # returned over the wire; we only expose the source text.
    "embed_source",
)

# Keys preserved on the lean shape — these match the existing
# `CustomerService.get_360` envelope so the frontend that consumes
# `/api/customers/{id}/profile?rich=false` sees the same fields it
# already understands.
_LEAN_KEYS: tuple[str, ...] = (
    "customer_id",
    "customer_type",
    "account_id",
    "parent_account_id",
    "name",
    "ic_number",
    "tier",
    "contact",
    "address",
    "subscriptions",
    "active_promotions",
    "entitlements",
    "recent_transactions",
    "open_cases",
    "latest_features",
    "recent_support",
    "total_monthly_value_myr",
    "lifetime_quarantine_count",
    "recommendations",
    "embed_source",
)


class Customer360Service:
    """Composition layer for the 360 endpoint.

    Construct with a `CustomerRepository`; the service does not own any
    other dependencies — every field of the rich shape is already
    materialised on the customer document by the PR-9 enrichment worker.
    """

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self._customers = customer_repo

    async def get_profile(
        self,
        customer_id: str,
        *,
        customer_type: str | None = None,
        rich: bool | None = None,
    ) -> dict:
        """Return the RetailGroup-tier rich profile or the lean envelope.

        - When `rich is None`, defaults to `flags.RICH_CUSTOMER_360`.
        - `customer_type` is an explicit dispatch hint that bypasses the
          `customer_index` lookup (mirrors the legacy 360 route).
        - Raises `CustomerNotFound` when the customer document is
          missing in both typed collections (and the legacy alias when
          `STORAGE_SPLIT` is off).
        """
        view = await self._customers.get_by_customer_id(
            customer_id, customer_type=customer_type  # type: ignore[arg-type]
        )
        if not view:
            raise CustomerNotFound(customer_id)

        # Drop Mongo internals before any further shaping.
        view.pop("_id", None)

        use_rich = flags.RICH_CUSTOMER_360 if rich is None else bool(rich)
        if use_rich:
            return self._shape_rich(view)
        return self._shape_lean(view)

    # ------------------------------------------------------------------
    # Shape helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _shape_rich(view: dict) -> dict:
        """Project the doc to the V3 rich envelope."""
        out: dict = {}
        for key in _RICH_KEYS:
            if key in view:
                out[key] = view[key]
        return out

    @staticmethod
    def _shape_lean(view: dict) -> dict:
        """Project the doc to the legacy lean envelope.

        Mirrors the field set the existing frontend expects from
        `CustomerService.get_360`. Rich-only keys (entities,
        cross_entity_metrics, brand_journey, …) are excluded so the
        flag-off response is bit-stable against PR-2 behaviour.
        """
        out: dict = {}
        for key in _LEAN_KEYS:
            if key in view:
                out[key] = view[key]
        return out
