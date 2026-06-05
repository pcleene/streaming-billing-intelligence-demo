"""Feature flags for the schema-richness refactor (master plan PR-1).

All flags default to OFF. They are read from environment variables once at
module-load. Flip in `.env` (or process env) once the corresponding migration
has been verified in the target environment.

Flags
-----
- `STORAGE_SPLIT`            — PR-2. When True, customer reads/writes dispatch
                               to `customers_residential` / `customers_commercial`
                               via `customer_index`. When False, the legacy
                               `customers` collection is used.
- `RICH_CUSTOMER_360`        — PR-9. When True, 360 endpoints return the
                               rich V3 customer document (entities,
                               cross_entity_metrics, etc.).
- `AI_ASSIST_AUTO_RUN`       — PR-8. When True, `case_lifecycle_worker`
                               auto-fires `ai_assist_service.generate(case_id)`
                               on every new case. When False, only the
                               explicit POST /api/cases/{id}/ai-assist route
                               triggers it.
- `AI_ASSIST_AGENTIC`        — PR-AG. When True, `AiAssistService.generate`
                               routes through the LangGraph-driven
                               `AssistAgent` instead of the linear
                               `RagService.assist` path, and the
                               `assist_agent_worker` consumes
                               `quarantine_cases` change-stream insert
                               events to fire the agent on every new case.
                               When False, the legacy linear path is used.

Conventions
-----------
- Read via the `FeatureFlags` singleton: `from app.core.feature_flags import flags`.
- Tests should use `monkeypatch.setattr(flags, "STORAGE_SPLIT", True)` to flip
  per-test; do not rely on env var reload.
- After PR-15 cleanup, every `if flags.X:` branch becomes unconditional and
  the flag is removed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class FeatureFlags:
    STORAGE_SPLIT: bool = False
    RICH_CUSTOMER_360: bool = False
    AI_ASSIST_AUTO_RUN: bool = False
    AI_ASSIST_AGENTIC: bool = False

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        return cls(
            STORAGE_SPLIT=_env_bool("FF_STORAGE_SPLIT", False),
            RICH_CUSTOMER_360=_env_bool("FF_RICH_CUSTOMER_360", False),
            AI_ASSIST_AUTO_RUN=_env_bool("FF_AI_ASSIST_AUTO_RUN", False),
            AI_ASSIST_AGENTIC=_env_bool("FF_AI_ASSIST_AGENTIC", False),
        )


flags = FeatureFlags.from_env()
