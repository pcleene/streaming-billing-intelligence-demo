"""Customer 360 orchestration.

Composes the customer_360 aggregation, normalises the result for the API
layer, and applies the demo's CRM-lag toggle (simulates a stale data-warehouse
view to motivate the operational-data-platform narrative).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.errors import CustomerNotFound
from app.core.logging import get_logger
from app.repositories.customer_repo import CustomerRepository

logger = get_logger(__name__)


class CustomerService:
    def __init__(self, customer_repo: CustomerRepository) -> None:
        self._customers = customer_repo

    async def get_360(
        self,
        customer_id: str,
        *,
        simulate_crm_lag: bool = False,
        customer_type: str | None = None,
    ) -> dict:
        # Phase A (ADR-011) + PR-2 (ADR-020/021): every 360 dependency is
        # embedded on the typed customer doc, so a single find_one replaces
        # the four-stage $lookup pipeline. Type dispatch happens via
        # `customer_index` inside the repo when STORAGE_SPLIT is on.
        view = await self._customers.get_by_customer_id(
            customer_id, customer_type=customer_type  # type: ignore[arg-type]
        )
        if not view:
            raise CustomerNotFound(customer_id)
        view.pop("_id", None)
        view["crm_lag"] = self._crm_lag_info(simulate=simulate_crm_lag)
        if simulate_crm_lag:
            view = self._mask_for_lag(view)
        return view

    async def search(self, q: str, *, limit: int = 20) -> list[dict]:
        return await self._customers.search(q, limit=limit)

    async def page(self, *, skip: int = 0, limit: int = 50) -> list[dict]:
        return await self._customers.page(skip=skip, limit=limit)

    # ----- helpers -----------------------------------------------------
    def _crm_lag_info(self, *, simulate: bool) -> dict:
        if not simulate:
            return {"simulated": False, "lag_hours": 0, "snapshot_at": datetime.now(timezone.utc).isoformat()}
        snap = datetime.now(timezone.utc) - timedelta(hours=settings.crm_lag_hours)
        return {
            "simulated": True,
            "lag_hours": settings.crm_lag_hours,
            "snapshot_at": snap.isoformat(),
            "note": "Simulated CRM data warehouse lag — operational platform shows live state.",
        }

    def _mask_for_lag(self, view: dict) -> dict:
        """Drop fields that wouldn't have arrived in a stale CRM snapshot yet."""
        view = dict(view)
        view["recent_transactions"] = []
        view["open_cases"] = []
        view["latest_features"] = None
        return view
