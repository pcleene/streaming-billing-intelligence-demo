"""Helpers for translating live rule documents into ASP-equivalent shapes.

The actual ASP pipelines are defined in JS under `infra/asp/`. This module
exposes the small set of metadata the rule-change watcher worker uses to
log diffs when an analyst toggles a rule between shadow/active modes.
"""

from __future__ import annotations

from app.pipelines.rule_pipeline_builders import build_rule_pipeline


def preview_rule_pipeline(rule_doc: dict) -> list[dict]:
    """Materialise the batch-equivalent aggregation for a rule document.

    Used by the rule-change watcher when emitting an SSE event so the
    frontend can show 'this is the pipeline that just went active'.
    """
    return build_rule_pipeline(rule_doc["rule_type"], rule_doc.get("parameters", {}))
