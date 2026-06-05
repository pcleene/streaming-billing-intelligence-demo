"""Analyst-facing transaction-pattern service (PR-12).

Thin facade over `TransactionRepository.customer_pattern` that:

  - validates the customer exists (so the route layer can return a
    proper 404 envelope rather than a falsely-empty pattern dict),
  - clamps the `days` lookback into [1, 365] to keep accidental misuse
    of the API (e.g. `days=0` or `days=10000`) from producing
    misleading shapes, and
  - returns the canonical pattern dict produced by the repo.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository
from app.repositories.transaction_repo import TransactionRepository

logger = get_logger(__name__)


# Hard bounds on the lookback window. Mirrors the agent's analytics
# tooling which also caps the window for cost-control reasons. Service-
# layer clamp: callers (route or agent) get a deterministic shape
# regardless of how the upstream UI/agent chose to encode the bound.
DAYS_MIN: int = 1
DAYS_MAX: int = 365


def _clamp_days(days: int) -> int:
    if days < DAYS_MIN:
        return DAYS_MIN
    if days > DAYS_MAX:
        return DAYS_MAX
    return days


class TransactionAnalyticsService:
    """Service-layer entry point for customer-scope transaction analytics."""

    def __init__(
        self,
        txn_repo: TransactionRepository,
        customer_repo: CustomerRepository,
    ) -> None:
        self._txn_repo = txn_repo
        self._customers = customer_repo

    async def customer_pattern(
        self, customer_id: str, *, days: int = 30
    ) -> dict | None:
        """Return the customer's transaction-pattern summary or `None`.

        - Validates customer existence first so missing customers
          surface as `None` (route layer translates to 404).
        - Clamps `days` into [1, 365].
        - Delegates to `TransactionRepository.customer_pattern`.
        """
        # Customer existence — single round-trip, no projection (the
        # repo already strips `embedding` from response shaping in the
        # 360 service; here we just need a hit/miss).
        customer = await self._customers.get_by_customer_id(customer_id)
        if customer is None:
            return None

        clamped = _clamp_days(int(days))
        return await self._txn_repo.customer_pattern(
            customer_id, days=clamped
        )


__all__ = [
    "DAYS_MAX",
    "DAYS_MIN",
    "TransactionAnalyticsService",
]
