"""Analyst-facing schemas — RAG/AI assist output."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import Disposition


class CitedReference(BaseModel):
    """One cited historical case."""
    model_config = ConfigDict(populate_by_name=True)
    case_id: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    disposition: Disposition
    short_reason: str        # 1-line description of why it's similar
    rules_triggered: list[str] = Field(default_factory=list)


class AnalystAssistOutput(BaseModel):
    """Structured Bedrock-Claude output. The LLM is constrained to this shape.

    This is the schema we hand to the LLM via tool-use / structured output;
    the response must validate against this model — no free-form blobs.
    """
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    summary: str = Field(min_length=20, max_length=600,
                         description="Plain-English summary of what's anomalous.")
    likelihood: Disposition = Field(
        description="Most likely categorisation."
    )
    confidence: float = Field(ge=0.0, le=1.0,
                              description="Confidence in the likelihood call (0-1).")
    rationale: str = Field(min_length=20, max_length=1200,
                           description="Why this likelihood — cite evidence.")
    recommended_steps: list[str] = Field(
        min_length=1, max_length=8,
        description="Numbered next-step recommendations for the analyst."
    )
    references: list[CitedReference] = Field(
        default_factory=list,
        description="Cited historical cases that informed the recommendation."
    )


class AnalystOverride(BaseModel):
    """Analyst's final disposition fed back into history."""
    case_id: str
    disposition: Disposition
    notes: str
    overrode_ai: bool = False
