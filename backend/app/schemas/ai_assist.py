"""AI-assist schemas (split out from quarantine for clarity in PR-1).

Re-exports the locked-down `AiAssist*` types defined in `quarantine.py`
(Phase B.4) and adds the `RecommendedAction` shape used by PR-8's
`ai_assist_service`. Importers should prefer this module going forward.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .quarantine import AiAssist, AiAssistCitation, AiAssistRetrieval

__all__ = [
    "AiAssist",
    "AiAssistCitation",
    "AiAssistRetrieval",
    "RecommendedAction",
]


class RecommendedAction(BaseModel):
    """One concrete next step the analyst (or an automation) should take."""
    action: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
