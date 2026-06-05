"""Rules repository — quarantine_rules collection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.constants import QUARANTINE_RULES
from app.repositories.base import BaseRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Top-level rule fields whose changes we want recorded in `version_history`.
# Identity / audit / metric fields are intentionally excluded — they are
# either immutable (`rule_id`, `created_at`) or noise that would flood the
# history (`metrics.*`, `updated_at`, `version`).
_DIFFABLE_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "rule_type",
    "severity",
    "enabled",
    "mode",
    "parameters",
    "ownership",
)


def _diff_fields(old: dict, new: dict) -> dict:
    """Return `{field: [old_value, new_value]}` for any DIFFABLE field that
    changed. Missing-on-old is recorded as `None`.
    """
    diff: dict = {}
    for f in _DIFFABLE_FIELDS:
        old_v = old.get(f)
        new_v = new.get(f, old_v)
        if old_v != new_v and f in new:
            diff[f] = [old_v, new_v]
    return diff


class RuleRepository(BaseRepository):
    COLLECTION_NAME = QUARANTINE_RULES

    # Documents are looked up by the application-level `rule_id` field
    # (set by seed/create), NOT by Mongo's auto-assigned `_id`. Keep this
    # consistent across all read+write helpers.
    ALLOWED_MODES: tuple[str, ...] = ("active", "shadow", "disabled")

    async def get_by_id(self, rule_id: str) -> dict | None:
        return await self.find_one({"rule_id": rule_id})

    async def get_by_name(self, name: str) -> dict | None:
        return await self.find_one({"name": name})

    async def list_all(
        self,
        *,
        rule_type: str | None = None,
        enabled: bool | None = None,
        mode: str | None = None,
    ) -> list[dict]:
        filter_: dict = {}
        if rule_type:
            filter_["rule_type"] = rule_type
        if enabled is not None:
            filter_["enabled"] = enabled
        if mode:
            filter_["mode"] = mode
        return await self.find_many(filter_, sort=[("name", 1)])

    async def list_active(self) -> list[dict]:
        """Active = enabled AND mode='active' (shadow rules don't quarantine)."""
        return await self.find_many({"enabled": True, "mode": "active"})

    async def upsert(self, doc: dict) -> dict:
        """Upsert by rule name. Returns the persisted document.

        Strips `created_at`/`_id` from the `$set` payload to avoid the
        MongoDB path conflict between `$set` and `$setOnInsert`, and to
        avoid attempts to mutate the immutable `_id`.
        """
        now = _utcnow()
        payload = {k: v for k, v in doc.items() if k not in ("_id", "created_at")}
        payload["updated_at"] = now
        await self.update_one(
            {"name": doc["name"]},
            {"$set": payload, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        persisted = await self.get_by_name(doc["name"])
        # find_one returns None only if a concurrent delete raced us; treat
        # that as a hard failure so the caller doesn't silently get None.
        if persisted is None:
            raise RuntimeError(f"rule disappeared after upsert: name={doc['name']!r}")
        return persisted

    async def update_with_history(
        self,
        rule_id: str,
        patch: dict,
        *,
        changed_by: str,
    ) -> dict:
        """Apply a partial update + record an immutable `version_history` entry.

        - Computes the diff between the existing doc and `patch` over a fixed
          allow-list of mutable fields (parameters, mode, severity, ...).
        - When the diff is non-empty, increments `version` and `$push`-es a
          `RuleVersionHistoryEntry`-shaped record, then `$set`s the patched
          fields atomically.
        - When the diff is empty, the call is a no-op aside from refreshing
          `updated_at` — callers can safely retry idempotent edits.

        Raises KeyError if the rule doesn't exist (the caller is expected to
        translate that into a domain-level NotFound).
        """
        existing = await self.get_by_id(rule_id)
        if existing is None:
            raise KeyError(rule_id)

        diff = _diff_fields(existing, patch)
        now = _utcnow()
        new_version = int(existing.get("version") or 1)
        update: dict[str, Any]
        if diff:
            new_version += 1
            history_entry = {
                "version": new_version,
                "changed_at": now,
                "changed_by": changed_by,
                "diff": diff,
            }
            set_doc: dict[str, Any] = {
                "updated_at": now,
                "version": new_version,
            }
            for f, (_, new_v) in diff.items():
                set_doc[f] = new_v
            update = {
                "$set": set_doc,
                "$push": {"version_history": history_entry},
            }
        else:
            update = {"$set": {"updated_at": now}}
        await self.update_one({"rule_id": rule_id}, update)
        persisted = await self.get_by_id(rule_id)
        if persisted is None:
            raise RuntimeError(f"rule disappeared after update: rule_id={rule_id!r}")
        return persisted

    async def increment_hit(self, rule_id: str) -> int:
        return await self.update_one(
            {"rule_id": rule_id},
            {
                "$inc": {"metrics.hit_count_24h": 1, "metrics.hit_count_total": 1},
                "$set": {"metrics.last_hit_at": _utcnow()},
            },
        )

    async def set_mode(self, rule_id: str, mode: str) -> int:
        if mode not in self.ALLOWED_MODES:
            raise ValueError(f"invalid mode: {mode!r}; expected one of {self.ALLOWED_MODES}")
        return await self.update_one(
            {"rule_id": rule_id},
            {"$set": {"mode": mode, "updated_at": _utcnow()}},
        )

    async def set_enabled(self, rule_id: str, enabled: bool) -> int:
        return await self.update_one(
            {"rule_id": rule_id},
            {"$set": {"enabled": enabled, "updated_at": _utcnow()}},
        )
