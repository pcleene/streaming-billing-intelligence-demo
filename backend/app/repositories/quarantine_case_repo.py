"""Quarantine cases repository — live cases only.

Historical / resolved corpus → CaseHistoryRepository.

PR-7 layers a richer lifecycle on top of the lean PR-1 surface:
  - `open_case`        builds the V3 case shape (lifecycle, sla, priority,
                       revenue_impact, customer + transaction snapshots),
                       inserts the parent doc, and mirrors a rich embed
                       onto `customers.open_cases` + bumps
                       `lifetime_quarantine_count`.
  - `transition_status` flips the status (open ↔ under_review), appends a
                       `LifecycleEvent`, and syncs the customer embed
                       status via array_filters.
  - `resolve`          terminal: sets disposition/resolved_at, appends a
                       `LifecycleEvent`, and `$pull`s the embed off the
                       customer doc (the parent case stays in
                       quarantine_cases until PR-8 archives to history).
  - `apply_sla_update` is what `case_lifecycle_worker` calls each minute.

The legacy lean methods (`sync_open_case_to_customer`,
`update_open_case_status_on_customer`, `remove_open_case_from_customer`,
`update_disposition`, `attach_ai_assist`) are preserved unchanged for
the change-stream callers and the existing PR-1 tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.constants import (
    CUSTOMERS,
    CUSTOMERS_COMMERCIAL,
    CUSTOMERS_RESIDENTIAL,
    QUARANTINE_CASES,
    SCHEMA_VERSION_V3,
)
from app.repositories.base import BaseRepository
from app.services.case_lifecycle import (
    apply_sla_tick,
    compute_priority,
    initial_sla,
    open_lifecycle_event,
    revenue_impact_from_summary,
    transition_lifecycle_event,
)

# Statuses we keep mirrored on the customer's open_cases embed.
EMBED_OPEN_STATUSES: tuple[str, ...] = ("open", "under_review")
TERMINAL_STATUSES: tuple[str, ...] = ("resolved", "dismissed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_open_case_embed(case: dict) -> dict:
    """Project a case doc to the OpenCaseEmbed shape.

    Includes `priority_score` / `priority_band` / `sla.target_resolution_at`
    when present (V3 shape), so the customer 360 tile can render queue
    position without descending into the parent case doc.
    """
    rule_types = sorted({
        r.get("rule_type")
        for r in case.get("rules_triggered", [])
        if isinstance(r, dict) and r.get("rule_type")
    })
    embed: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "transaction_id": case.get("transaction_id"),
        "severity": case.get("severity"),
        "status": case.get("status", "open"),
        "rule_types": rule_types,
        "created_at": case.get("created_at") or _utcnow(),
        "updated_at": case.get("updated_at") or _utcnow(),
    }
    if "priority_score" in case:
        embed["priority_score"] = case["priority_score"]
    if "priority_band" in case:
        embed["priority_band"] = case["priority_band"]
    sla = case.get("sla") or {}
    if "target_resolution_at" in sla:
        embed["sla_target_at"] = sla["target_resolution_at"]
    return embed


class QuarantineCaseRepository(BaseRepository):
    COLLECTION_NAME = QUARANTINE_CASES

    async def get_by_id(self, case_id: str) -> dict | None:
        return await self.find_one({"case_id": case_id})

    async def list_paged(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        rule_type: str | None = None,
        priority_band: str | None = None,
        agent_reviewed: bool | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        filter_: dict = {}
        if status:
            filter_["status"] = status
        if severity:
            filter_["severity"] = severity
        if rule_type:
            filter_["rules_triggered.rule_type"] = rule_type
        if priority_band:
            filter_["priority_band"] = priority_band
        if agent_reviewed is True:
            # "non-empty agent_trace array" — $exists keeps the field
            # required, $ne: [] rules out the empty-array sentinel. The
            # field is always either missing/null or a list, so we
            # don't need a separate $type guard.
            filter_["ai_assist.agent_trace"] = {
                "$exists": True,
                "$ne": [],
            }
        elif agent_reviewed is False:
            filter_["$or"] = [
                {"ai_assist": None},
                {"ai_assist.agent_trace": {"$exists": False}},
                {"ai_assist.agent_trace": []},
            ]
        return await self.find_many(
            filter_,
            sort=[("priority_score", -1), ("created_at", -1)],
            skip=skip,
            limit=limit,
        )

    async def list_open(self, *, limit: int = 1000) -> list[dict]:
        """Live (non-terminal) cases, sorted by SLA breach risk first."""
        return await self.find_many(
            {"status": {"$in": list(EMBED_OPEN_STATUSES)}},
            sort=[("sla.minutes_to_breach", 1)],
            limit=limit,
        )

    async def count_filtered(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        rule_type: str | None = None,
        priority_band: str | None = None,
        agent_reviewed: bool | None = None,
    ) -> int:
        filter_: dict = {}
        if status:
            filter_["status"] = status
        if severity:
            filter_["severity"] = severity
        if rule_type:
            filter_["rules_triggered.rule_type"] = rule_type
        if priority_band:
            filter_["priority_band"] = priority_band
        if agent_reviewed is True:
            # "non-empty agent_trace array" — $exists keeps the field
            # required, $ne: [] rules out the empty-array sentinel. The
            # field is always either missing/null or a list, so we
            # don't need a separate $type guard.
            filter_["ai_assist.agent_trace"] = {
                "$exists": True,
                "$ne": [],
            }
        elif agent_reviewed is False:
            filter_["$or"] = [
                {"ai_assist": None},
                {"ai_assist.agent_trace": {"$exists": False}},
                {"ai_assist.agent_trace": []},
            ]
        return await self.count(filter_)

    # --- PR-AG analytics ----------------------------------------------

    async def rule_type_frequency(
        self, rule_type: str, *, days: int = 7
    ) -> dict:
        """How often a rule_type fired in the last N days.

        Returns:
            ``{
                "rule_type": str,
                "window_days": int,
                "total_cases": int,
                "by_disposition": {disposition: count, ...},
                "by_severity": {severity: count, ...},
                "open_count": int,
            }``

        Implementation: pulls the matching cases via ``find_many``
        (bounded by ``rules_triggered.rule_type`` + ``created_at``
        window — both indexed in production) and folds the three
        distributions in Python. We avoid a single ``$facet``
        aggregation so the FakeDB-backed unit tests don't need an
        aggregation engine.

        Note: cases are matched on ``created_at`` (the field actually
        persisted by ``open_case`` — the spec sometimes refers to it
        as ``opened_at``).
        """
        since = _utcnow() - timedelta(days=days)
        # Filter by created_at in the query; do the array-of-rule-types
        # match in Python so we don't depend on engine support for
        # dotted-array projections in unit-test fakes.
        candidates = await self.find_many(
            {"created_at": {"$gte": since}},
        )
        rows = [
            c for c in candidates
            if any(
                isinstance(r, dict) and r.get("rule_type") == rule_type
                for r in (c.get("rules_triggered") or [])
            )
        ]
        by_disposition: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        open_count = 0
        for case in rows:
            disp = case.get("disposition") or "open"
            by_disposition[disp] = by_disposition.get(disp, 0) + 1
            sev = case.get("severity") or "unknown"
            by_severity[sev] = by_severity.get(sev, 0) + 1
            if case.get("status") in EMBED_OPEN_STATUSES:
                open_count += 1
        return {
            "rule_type": rule_type,
            "window_days": days,
            "total_cases": len(rows),
            "by_disposition": by_disposition,
            "by_severity": by_severity,
            "open_count": open_count,
        }

    # --- PR-7 V3 lifecycle --------------------------------------------

    async def open_case(
        self,
        *,
        case_id: str,
        customer_id: str,
        severity: str,
        rules_triggered: list[dict],
        customer_doc: dict | None = None,
        transaction_summary: dict | None = None,
        customer_snapshot: dict | None = None,
        transaction_id: str | None = None,
        cycle_id: str | None = None,
        actor_id: str = "system",
        sync_customer_embed: bool = True,
        tags: list[str] | None = None,
    ) -> dict:
        """Insert a fresh V3 case + mirror onto `customers.open_cases`.

        `customer_doc` is the source-of-truth customer document used to
        derive `customer_snapshot` / `customer_type` / repeat-offender
        signals. When supplied as None, we project a minimal snapshot
        from the kwargs.
        """
        now = _utcnow()
        ctype = None
        lifetime_count = 0
        is_commercial = False
        if customer_doc is not None:
            ctype = customer_doc.get("customer_type") or customer_doc.get("_resolved_type")
            lifetime_count = int(customer_doc.get("lifetime_quarantine_count") or 0)
            is_commercial = (ctype == "commercial")
            if customer_snapshot is None:
                customer_snapshot = self._project_customer_snapshot(customer_doc)

        revenue_impact = revenue_impact_from_summary(
            transaction_summary=transaction_summary, severity=severity
        )
        score, band, drivers = compute_priority(
            severity=severity,
            amount_at_risk_myr=revenue_impact["amount_at_risk_myr"],
            customer_lifetime_quarantine_count=lifetime_count,
            is_commercial=is_commercial,
        )
        sla = initial_sla(severity=severity, opened_at=now)
        rule_types = sorted({
            r.get("rule_type") for r in rules_triggered
            if isinstance(r, dict) and r.get("rule_type")
        })
        lifecycle = [open_lifecycle_event(
            actor_id=actor_id, rule_types_fired=rule_types
        )]
        case_doc: dict[str, Any] = {
            "_schema_version": SCHEMA_VERSION_V3,
            "case_id": case_id,
            "customer_id": customer_id,
            "customer_type": ctype,
            "transaction_id": transaction_id,
            "cycle_id": cycle_id,
            "severity": severity,
            "status": "open",
            "priority_score": score,
            "priority_band": band,
            "auto_priority_drivers": drivers,
            "lifecycle": lifecycle,
            "rules_triggered": rules_triggered,
            "customer_snapshot": customer_snapshot,
            "transaction_summary": transaction_summary,
            "ai_assist": None,
            "similar_cases_preview": [],
            "analyst_notes": None,
            "disposition": None,
            "resolved_at": None,
            "resolved_by": None,
            "sla": sla,
            "revenue_impact": revenue_impact,
            "tags": tags or [],
            "created_at": now,
            "updated_at": now,
        }
        await self.insert_one(case_doc)
        if sync_customer_embed:
            await self.sync_open_case_to_customer(case_doc)
        return case_doc

    async def transition_status(
        self,
        case_id: str,
        *,
        to_status: str,
        actor_id: str,
        actor_type: str = "analyst",
        note: str | None = None,
        sync_customer_embed: bool = True,
    ) -> dict:
        """Flip a case between `open` and `under_review`.

        Appends a `LifecycleEvent` and (when requested) syncs the embed
        status on the customer doc. Terminal transitions go through
        `resolve()` instead.
        """
        if to_status not in EMBED_OPEN_STATUSES:
            raise ValueError(
                f"transition_status only accepts {EMBED_OPEN_STATUSES}; "
                f"use resolve() for terminal statuses"
            )
        existing = await self.get_by_id(case_id)
        if existing is None:
            raise KeyError(case_id)
        from_status = existing.get("status", "open")
        if from_status == to_status:
            return existing
        now = _utcnow()
        event = transition_lifecycle_event(
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            actor_type=actor_type,
            note=note,
        )
        await self.update_one(
            {"case_id": case_id},
            {
                "$set": {"status": to_status, "updated_at": now},
                "$push": {"lifecycle": event},
            },
        )
        if sync_customer_embed and existing.get("customer_id"):
            await self.update_open_case_status_on_customer(
                customer_id=existing["customer_id"],
                case_id=case_id,
                status=to_status,
            )
        persisted = await self.get_by_id(case_id)
        if persisted is None:
            raise RuntimeError(f"case disappeared after transition: {case_id!r}")
        return persisted

    async def resolve(
        self,
        case_id: str,
        *,
        disposition: str,
        analyst_id: str,
        analyst_notes: str | None = None,
        terminal_status: str = "resolved",
        sync_customer_embed: bool = True,
    ) -> dict:
        """Apply analyst disposition and remove from the customer's embed.

        `terminal_status` is `resolved` or `dismissed`. The parent case
        stays in `quarantine_cases` (PR-8 archives to history).
        """
        if terminal_status not in TERMINAL_STATUSES:
            raise ValueError(
                f"resolve() requires terminal_status in {TERMINAL_STATUSES}, "
                f"got {terminal_status!r}"
            )
        existing = await self.get_by_id(case_id)
        if existing is None:
            raise KeyError(case_id)
        now = _utcnow()
        from_status = existing.get("status", "open")
        event = transition_lifecycle_event(
            from_status=from_status,
            to_status=terminal_status,
            actor_id=analyst_id,
            actor_type="analyst",
            note=analyst_notes,
        )
        await self.update_one(
            {"case_id": case_id},
            {
                "$set": {
                    "status": terminal_status,
                    "disposition": disposition,
                    "analyst_notes": analyst_notes,
                    "resolved_by": analyst_id,
                    "resolved_at": now,
                    "updated_at": now,
                },
                "$push": {"lifecycle": event},
            },
        )
        if sync_customer_embed and existing.get("customer_id"):
            await self.remove_open_case_from_customer(
                customer_id=existing["customer_id"], case_id=case_id
            )
        persisted = await self.get_by_id(case_id)
        if persisted is None:
            raise RuntimeError(f"case disappeared after resolve: {case_id!r}")
        return persisted

    async def apply_sla_update(self, case_id: str, *, sla: dict) -> int:
        """Persist a fresh SLA fragment computed by `case_lifecycle_worker`."""
        return await self.update_one(
            {"case_id": case_id},
            {"$set": {"sla": sla, "updated_at": _utcnow()}},
        )

    async def sweep_sla(self, *, now: datetime | None = None) -> dict:
        """Recompute SLA for every open case. Returns counters for logging.

        Pulled into the repo (rather than the worker) so unit tests can
        drive a full sweep against the FakeDB without spinning up an
        asyncio task. The worker just calls this in a loop.
        """
        now = now or _utcnow()
        scanned = 0
        breached = 0
        updated = 0
        for case in await self.list_open(limit=10_000):
            scanned += 1
            sla = case.get("sla") or {}
            opened_at = case.get("created_at") or now
            new_sla = apply_sla_tick(sla=sla, opened_at=opened_at, now=now)
            if new_sla == sla:
                continue
            await self.apply_sla_update(case["case_id"], sla=new_sla)
            updated += 1
            if new_sla.get("is_breached") and not sla.get("is_breached"):
                breached += 1
        return {"scanned": scanned, "updated": updated, "newly_breached": breached}

    @staticmethod
    def _project_customer_snapshot(customer: dict) -> dict:
        """Project a flat-root V3 customer doc to `CustomerSnapshotV3` shape."""
        return {
            "customer_id":      customer.get("customer_id"),
            "name":             customer.get("name") or "",
            "tier":             customer.get("tier"),
            "ic_number":        customer.get("ic_number"),
            "loyalty_member_id": customer.get("loyalty_member_id"),
            "package_at_billing": (
                customer.get("active_subscriptions") or [{}]
            )[0].get("package_code") if customer.get("active_subscriptions") else None,
            "active_promotions_at_billing": list(customer.get("active_promotions") or []),
            "active_entitlements_at_billing": [
                e.get("content_id") for e in (customer.get("entitlements") or [])
                if isinstance(e, dict) and e.get("content_id")
            ],
            "lifetime_quarantine_count": int(customer.get("lifetime_quarantine_count") or 0),
            "tenure_months":    int(customer.get("tenure_months") or 0),
            "churn_risk":       float(customer.get("churn_risk") or 0.0),
            "service_state":    (customer.get("address") or {}).get("state"),
        }

    # --- PR-1 lean surface (unchanged) --------------------------------

    async def update_disposition(
        self,
        case_id: str,
        *,
        disposition: str,
        analyst_id: str,
        analyst_notes: str | None = None,
        status: str = "resolved",
        resolved_at: datetime | None = None,
    ) -> int:
        """Apply analyst disposition to a case (lean variant retained for
        legacy callers; `resolve()` is the PR-7 path).
        """
        now = _utcnow()
        set_doc: dict = {
            "status": status,
            "disposition": disposition,
            "analyst_notes": analyst_notes,
            "resolved_by": analyst_id,
            "updated_at": now,
        }
        if resolved_at is not None:
            set_doc["resolved_at"] = resolved_at
        return await self.update_one({"case_id": case_id}, {"$set": set_doc})

    async def attach_ai_assist(self, case_id: str, ai_assist: dict) -> int:
        return await self.update_one(
            {"case_id": case_id},
            {"$set": {"ai_assist": ai_assist, "updated_at": _utcnow()}},
        )

    # --- customers.open_cases mirror -----------------------------------
    async def sync_open_case_to_customer(self, case: dict) -> int:
        """Push a freshly-opened case into customers.open_cases and bump the
        lifetime counter. Idempotent re-pushes will duplicate — callers
        (change-stream tail) see each insert exactly once.

        Mirrors to whichever typed collection the customer lives in
        (`customer_type`); falls back to the legacy `CUSTOMERS` alias.
        """
        cid = case.get("customer_id")
        if not cid:
            return 0
        embed = _build_open_case_embed(case)
        coll_name = self._mirror_collection_for(case)
        result = await self._db[coll_name].update_one(
            {"customer_id": cid},
            {
                "$push": {"open_cases": embed},
                "$inc": {"lifetime_quarantine_count": 1},
            },
        )
        return result.modified_count

    async def update_open_case_status_on_customer(
        self, *, customer_id: str, case_id: str, status: str,
        customer_type: str | None = None,
    ) -> int:
        """Flip the embedded case's status (open ↔ under_review) on the
        customer doc. Uses array_filters so we don't touch other entries.
        """
        if status not in EMBED_OPEN_STATUSES:
            raise ValueError(f"status must be one of {EMBED_OPEN_STATUSES}, got {status!r}")
        coll_name = self._mirror_collection_for({"customer_type": customer_type})
        result = await self._db[coll_name].update_one(
            {"customer_id": customer_id},
            {
                "$set": {
                    "open_cases.$[c].status": status,
                    "open_cases.$[c].updated_at": _utcnow(),
                }
            },
            array_filters=[{"c.case_id": case_id}],
        )
        return result.modified_count

    async def remove_open_case_from_customer(
        self, *, customer_id: str, case_id: str,
        customer_type: str | None = None,
    ) -> int:
        """$pull the embed when the parent case is resolved/dismissed."""
        coll_name = self._mirror_collection_for({"customer_type": customer_type})
        result = await self._db[coll_name].update_one(
            {"customer_id": customer_id},
            {"$pull": {"open_cases": {"case_id": case_id}}},
        )
        return result.modified_count

    @staticmethod
    def _mirror_collection_for(case_or_hint: dict) -> str:
        """Pick the customer collection to mirror into based on
        `customer_type`. Defaults to the legacy alias so callers that
        don't yet pass type still hit the residential coll.
        """
        ctype = (case_or_hint or {}).get("customer_type")
        if ctype == "commercial":
            return CUSTOMERS_COMMERCIAL
        if ctype == "residential":
            return CUSTOMERS_RESIDENTIAL
        return CUSTOMERS
