"""Structured output schema for analyst assist.

We constrain Claude via a JSON schema (used as an Anthropic tool definition).
This guarantees the UI gets a predictable shape: summary, likelihood enum,
confidence float, rationale bullets, recommended steps, and citations back
to retrieved cases. No free-form prose anywhere downstream.
"""

from __future__ import annotations

ANALYST_ASSIST_TOOL_NAME = "submit_analyst_assist"

ANALYST_ASSIST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "1-2 sentence plain-English summary for the analyst.",
        },
        "likelihood": {
            "type": "string",
            "enum": ["true_positive", "false_positive", "needs_more_info"],
            "description": "Best guess of the disposition.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "rationale": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Bullet-point reasoning citing retrieved cases or rule evidence.",
        },
        "recommended_steps": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Concrete next actions for the analyst.",
        },
        "references": {
            "type": "array",
            "description": "Cited historical case_ids and the disposition each shows.",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "disposition": {"type": "string"},
                    "score": {"type": "number"},
                    "why_relevant": {"type": "string"},
                },
                "required": ["case_id", "disposition"],
            },
        },
    },
    "required": ["summary", "likelihood", "confidence", "rationale", "recommended_steps", "references"],
}


def analyst_assist_tool() -> dict:
    """Return the Anthropic tool spec that forces structured output."""
    return {
        "name": ANALYST_ASSIST_TOOL_NAME,
        "description": (
            "Submit your analyst assistance recommendation. You MUST call this "
            "tool exactly once. Do not respond with free-form text."
        ),
        "input_schema": ANALYST_ASSIST_SCHEMA,
    }
