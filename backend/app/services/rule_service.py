"""Rule administration + 'test against historical' service.

Backs the Rule Studio. Validates parameters via the Pydantic discriminated
union, persists rules, flips shadow ↔ active, and runs a builder pipeline
against a sample of recent transactions for the preview UI.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import DuplicateRuleName, RuleNotFound, RuleValidationError
from app.core.logging import get_logger
from app.pipelines.rule_pipeline_builders import RULE_BUILDERS, build_rule_pipeline
from app.repositories.rule_repo import RuleRepository
from app.repositories.transaction_repo import TransactionRepository
from app.schemas.rule import RuleCreate, RuleUpdate

logger = get_logger(__name__)


class RuleService:
    def __init__(
        self,
        rule_repo: RuleRepository,
        transaction_repo: TransactionRepository,
    ) -> None:
        self._rules = rule_repo
        self._txns = transaction_repo

    async def list_rules(self) -> list[dict]:
        # Drop the BSON `_id` so the FastAPI JSON encoder doesn't trip over
        # `ObjectId`. The repo intentionally keeps it for callers that need
        # to update by `_id`; the route returns plain JSON.
        rules = await self._rules.list_all()
        return [{k: v for k, v in r.items() if k != "_id"} for r in rules]

    async def get(self, rule_id: str) -> dict:
        rule = await self._rules.get_by_id(rule_id)
        if not rule:
            raise RuleNotFound(rule_id)
        # Mirror `list_rules`: strip Mongo `_id` (BSON ObjectId) so the
        # route can serialize this as plain JSON.
        return {k: v for k, v in rule.items() if k != "_id"}

    async def create(self, payload: RuleCreate) -> dict:
        if await self._rules.get_by_name(payload.name):
            raise DuplicateRuleName(payload.name)
        doc = payload.model_dump()
        # `rule_type` lives on the parameters discriminator (ADR-006); hoist
        # it to the top level so `list_all(rule_type=...)` filters and the
        # rule_repo dispatch can read it without descending into parameters.
        params = doc.get("parameters") or {}
        rt = params.get("rule_type")
        if rt is not None:
            doc["rule_type"] = rt
        doc["rule_id"] = f"rule_{uuid.uuid4().hex[:12]}"
        doc["hit_count"] = 0
        # `created_at` is set atomically by the repo via $setOnInsert; do not
        # set it here or MongoDB will reject the path conflict with $set.
        return await self._rules.upsert(doc)

    async def update(
        self,
        rule_id: str,
        payload: RuleUpdate,
        *,
        changed_by: str = "demo-user",
    ) -> dict:
        # Existence check — translates a missing rule into RuleNotFound.
        await self.get(rule_id)
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return await self.get(rule_id)
        return await self._rules.update_with_history(
            rule_id, patch, changed_by=changed_by
        )

    async def set_mode(self, rule_id: str, *, mode: str) -> dict:
        await self.get(rule_id)
        if mode not in self._rules.ALLOWED_MODES:
            raise RuleValidationError(f"invalid mode: {mode!r}")
        await self._rules.set_mode(rule_id, mode)
        return await self.get(rule_id)

    async def test_against_history(
        self,
        *,
        rule_type: str,
        parameters: dict,
        sample_size: int = 1000,
    ) -> dict[str, Any]:
        """Run the rule's batch-equivalent aggregation against the most recent
        N transactions and return the would-quarantine hits + counters.
        """
        if rule_type not in RULE_BUILDERS:
            raise RuleValidationError(f"unknown rule_type: {rule_type}")

        # Limit input first so the builder operates on the candidate window.
        prefix: list[dict] = [
            {"$sort": {"timestamp": -1}},
            {"$limit": sample_size},
        ]
        body = build_rule_pipeline(rule_type, parameters)
        pipeline = prefix + body
        hits = await self._txns.aggregate(pipeline)

        return {
            "rule_type": rule_type,
            "sample_size": sample_size,
            "hit_count": len(hits),
            "hit_rate": (len(hits) / sample_size) if sample_size else 0.0,
            "hits": hits[:50],  # cap UI payload
        }
