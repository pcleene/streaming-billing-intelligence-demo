"""Prompt templates for analyst assist.

Kept short, deterministic, and faithfully grounded in the retrieved cases.
The system prompt forbids invention; the user prompt presents the live case
plus K retrieved analogues with their dispositions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


_MAX_EVIDENCE_CHARS = 4000  # ~1k tokens; protects max_tokens budget

SYSTEM_PROMPT = (
    "You are an analyst-assist agent for Acme Malaysia's billing quarantine team. "
    "You triage flagged transactions by comparing them to historically-resolved cases. "
    "Rules:\n"
    " - Ground every claim in the provided rule evidence or the retrieved cases.\n"
    " - Never invent case_ids, dispositions, or numbers.\n"
    " - If retrieved cases disagree, surface the conflict in your rationale.\n"
    " - You MUST respond by calling the submit_analyst_assist tool exactly once.\n"
    " - Confidence calibration: 0.0-0.4 weak signal, 0.4-0.7 moderate, 0.7-1.0 strong precedent."
)


def _summarise_case(case: dict[str, Any]) -> str:
    rules = ", ".join(r.get("rule_type", "?") for r in case.get("rules_triggered", []))
    snap = case.get("customer_snapshot") or {}
    return (
        f"case_id={case.get('case_id')} "
        f"segment={snap.get('segment', 'unknown')} "
        f"amount={case.get('amount', 0):.2f} MYR "
        f"severity={case.get('severity', '?')} "
        f"rules=[{rules}]"
    )


def _summarise_history(c: dict[str, Any]) -> str:
    return (
        f"- case_id={c.get('case_id')} score={c.get('score', 0):.3f} "
        f"disposition={c.get('disposition', '?')} "
        f"notes={(c.get('analyst_notes') or '')[:240]}"
    )


_MAX_CUSTOMER_CHARS = 2400  # ~600 tokens; keep prompt budget bounded
_MAX_TRANSACTION_CHARS = 2400


def _shape_customer_for_prompt(customer: dict | None) -> dict:
    """Project the rich V3 customer doc to the high-signal subset.

    Drop high-volume / low-signal sub-docs (interaction_history,
    brand_journey full timelines) so the prompt stays inside the
    token budget. Analysts care about: who is this customer, what
    are they paying for, what's their dispute history.
    """
    if not customer:
        return {}
    keep = (
        "customer_id",
        "customer_type",
        "tier",
        "name",
        "account_id",
        "subscriptions",
        "active_promotions",
        "entitlements",
        "open_cases",
        "latest_features",
        "total_monthly_value_myr",
        "lifetime_quarantine_count",
        "cross_entity_metrics",
        "current_cycle",
    )
    return {k: customer[k] for k in keep if k in customer}


def _shape_transaction_for_prompt(transaction: dict | None) -> dict:
    """Project the V3 transaction doc to the rule-relevant subset.

    Keep the line-level + discount detail (the structural fingerprint
    of the dispute) and drop heavy raw-event blobs.
    """
    if not transaction:
        return {}
    keep = (
        "transaction_id",
        "customer_id",
        "transaction_type",
        "channel",
        "timestamp",
        "subtotal_myr",
        "total_discount_myr",
        "tax_myr",
        "total_myr",
        "items",
        "items_summary",
        "discounts_applied",
        "computed_signals",
        "rule_outcomes",
    )
    return {k: transaction[k] for k in keep if k in transaction}


def analyst_assist_messages(
    live_case: dict,
    similar_cases: list[dict],
    *,
    customer: dict | None = None,
    transaction: dict | None = None,
) -> list[dict]:
    """Return a Bedrock-Anthropic `messages` payload (system handled separately).

    The caller passes `system=SYSTEM_PROMPT` to invoke_model.

    `customer` and `transaction` are the live, rich docs hydrated by
    the agent's `load_live_state` node. When supplied, they're appended
    as additional context blocks the LLM can ground its rationale in
    (the case snapshot is a point-in-time projection; the live docs
    carry the freshest entitlements / per-line items / discount shape).
    """
    if similar_cases:
        history_block = "\n".join(_summarise_history(c) for c in similar_cases)
    else:
        history_block = "(no historical analogues retrieved above the score threshold)"

    rules = live_case.get("rules_triggered", []) or []
    evidence = json.dumps(rules, default=_json_default, ensure_ascii=False, indent=2)
    if len(evidence) > _MAX_EVIDENCE_CHARS:
        evidence = evidence[:_MAX_EVIDENCE_CHARS] + "\n…(truncated)"

    sections: list[str] = [
        f"LIVE CASE:\n{_summarise_case(live_case)}",
        f"RULE EVIDENCE (JSON):\n{evidence}",
    ]

    cust_shaped = _shape_customer_for_prompt(customer)
    if cust_shaped:
        cust_json = json.dumps(
            cust_shaped, default=_json_default, ensure_ascii=False, indent=2
        )
        if len(cust_json) > _MAX_CUSTOMER_CHARS:
            cust_json = cust_json[:_MAX_CUSTOMER_CHARS] + "\n…(truncated)"
        sections.append(f"CUSTOMER LIVE STATE (JSON):\n{cust_json}")

    txn_shaped = _shape_transaction_for_prompt(transaction)
    if txn_shaped:
        txn_json = json.dumps(
            txn_shaped, default=_json_default, ensure_ascii=False, indent=2
        )
        if len(txn_json) > _MAX_TRANSACTION_CHARS:
            txn_json = txn_json[:_MAX_TRANSACTION_CHARS] + "\n…(truncated)"
        sections.append(f"TRANSACTION LIVE DETAIL (JSON):\n{txn_json}")

    sections.append(
        f"RETRIEVED HISTORICAL CASES (top {len(similar_cases)}):\n{history_block}"
    )
    sections.append("Produce your structured recommendation now.")

    user = "\n\n".join(sections)
    return [{"role": "user", "content": user}]
