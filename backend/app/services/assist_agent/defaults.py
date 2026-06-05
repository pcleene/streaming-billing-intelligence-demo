"""Deterministic rule-based classifier + synthesizer.

These are the fallbacks the agent uses when:
  * Tests need a hermetic, no-LLM pipeline.
  * Production's LLM-backed classifier/synthesizer call fails — the
    graph swaps to these so the analyst always gets *something*
    (with `degraded=True` to flag the LLM path didn't run).

Pure functions, no I/O: safe to call from any node.

The synthesizer is intentionally rich for a deterministic fallback:
  * Per-rule-type rationale/steps pull from each pipeline's projected
    `evidence` so the analyst gets a concrete next-action narrative
    even when Bedrock is unreachable.
  * Likelihood/confidence are anchored on the historical TP/FP
    distribution in `retrieval` when present (rather than a flat
    classification → confidence table).
  * Cross-signal narration combines rule context + analytics +
    customer/transaction live state into one coherent story.
"""

from __future__ import annotations

from typing import Any, Callable


def default_classifier(case: dict) -> dict:
    """Rule-based case classifier.

    Decision matrix (matches PR-AG spec):
      complexity         := high if severity in {critical, high} and >=2 rules
                          else medium if >=1 rule
                          else low
      evidence_strength  := strong if any rule has evidence.confidence >= 0.8
                          else moderate if >=1 rule
                          else weak
      recommended_path   := deep when complexity in {high, medium}
                          else fast
    """
    rules = case.get("rules_triggered") or []
    severity = (case.get("severity") or "").lower()
    n_rules = len(rules)

    if severity in ("critical", "high") and n_rules >= 2:
        complexity = "high"
    elif n_rules >= 1:
        complexity = "medium"
    else:
        complexity = "low"

    confidences: list[float] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        ev = r.get("evidence") or {}
        conf = ev.get("confidence")
        try:
            if conf is not None:
                confidences.append(float(conf))
        except (TypeError, ValueError):
            continue

    if any(c >= 0.8 for c in confidences):
        evidence_strength = "strong"
    elif n_rules >= 1:
        evidence_strength = "moderate"
    else:
        evidence_strength = "weak"

    recommended_path = "deep" if complexity in ("high", "medium") else "fast"

    return {
        "complexity": complexity,
        "evidence_strength": evidence_strength,
        "recommended_path": recommended_path,
    }


# ---------------------------------------------------------------------
# Helpers for the rich rule-type-aware synthesizer
# ---------------------------------------------------------------------


def _fmt_myr(n: Any) -> str:
    """Format a MYR amount or em-dash when unknown."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return "—"
    try:
        return f"MYR {float(n):.2f}"
    except (TypeError, ValueError):
        return "—"


def _num(n: Any) -> float | None:
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return None
    try:
        return float(n)
    except (TypeError, ValueError):
        return None


def _shorten_id(s: Any, head: int = 12) -> str:
    if not isinstance(s, str) or not s:
        return str(s) if s is not None else ""
    return s if len(s) <= head + 3 else f"{s[:head]}…"


def _list_str(v: Any, max_items: int = 3) -> str:
    if not isinstance(v, list) or not v:
        return ""
    items = [str(x) for x in v if x is not None]
    if len(items) <= max_items:
        return ", ".join(items)
    return f"{', '.join(items[:max_items])} +{len(items) - max_items} more"


def _pct(num: Any, denom: Any) -> str | None:
    n = _num(num)
    d = _num(denom)
    if n is None or d is None or d == 0:
        return None
    return f"{(n / d) * 100:.0f}%"


def _rule_narrative_discount_mismatch(ev: dict) -> tuple[str, list[str]]:
    """Rationale line + targeted steps for a discount_mismatch fire.

    Pipeline projection (per `rule_pipeline_builders.build_discount_mismatch`):
      total_myr, total_discount_myr.
    """
    total = ev.get("total_myr")
    disc = ev.get("total_discount_myr")
    pct = _pct(disc, total)
    pct_str = f" ({pct})" if pct else ""
    line = (
        f"Discount of {_fmt_myr(disc)} applied on {_fmt_myr(total)}"
        f"{pct_str}, but `customer_summary.active_promotions_at_billing` "
        f"was empty at cycle close — no promo could justify it."
    )
    steps = [
        "Pull the customer's recent promotion grants and check whether "
        "any window straddled the billing timestamp (often a promo expired "
        "1–2 days before billing and the discount logic missed it).",
        "Compare the discount amount against the price-book's published "
        "promotional rates; an exact match suggests an orphaned promo_id, "
        "a non-match suggests manual adjustment.",
        "If the discount was intentional and approved off-cycle, attach "
        "the override ticket and close as false_positive.",
        "If unjustified, queue a chargeback adjustment for "
        f"{_fmt_myr(disc)} and flag the rule's promo lookup window for review.",
    ]
    return line, steps


def _rule_narrative_entitlement_mismatch(ev: dict) -> tuple[str, list[str]]:
    """Pipeline projects: purchased_refs (list), entitled (list), total_myr."""
    purchased = ev.get("purchased_refs") or []
    entitled = ev.get("entitled") or []
    missing = [p for p in purchased if p not in entitled] if isinstance(purchased, list) else []
    missing_str = _list_str(missing, max_items=3) or "(none extracted)"
    line = (
        f"PPV charge totalling {_fmt_myr(ev.get('total_myr'))} references "
        f"product_ref(s) not present in active entitlements: {missing_str}. "
        f"Customer had {len(entitled)} entitlement(s) active at billing."
    )
    steps = [
        f"Verify the entitlement-grant pipeline processed any orders for "
        f"{missing_str} before the billing cycle closed (look for a delayed "
        "fulfilment write).",
        "Check whether the content_id → entitlement mapping changed in the "
        "catalog around the billing timestamp — a recent rename produces "
        "exactly this pattern.",
        "If the customer truly purchased the content (e.g. via PPV self-serve "
        "and the grant simply failed), backfill the entitlement and close TP.",
        "If the charge looks accidental (test order, double-tap on remote), "
        "issue a credit and tag the case as a PPV UX issue.",
    ]
    return line, steps


def _rule_narrative_amount_outlier(ev: dict) -> tuple[str, list[str]]:
    """Pipeline projects: total_myr, _avg, _stddev."""
    v = _num(ev.get("total_myr"))
    avg = _num(ev.get("_avg") if ev.get("_avg") is not None else ev.get("avg"))
    sd = _num(ev.get("_stddev") if ev.get("_stddev") is not None else ev.get("stddev"))
    sigma_str = ""
    if v is not None and avg is not None and sd and sd > 0:
        sigma_str = f" — {abs((v - avg) / sd):.1f}σ from the customer's own mean"
    line = (
        f"Transaction value {_fmt_myr(ev.get('total_myr'))} vs the customer's "
        f"prior 90-day average {_fmt_myr(avg)} (σ={_fmt_myr(sd).replace('MYR ', '')})"
        f"{sigma_str}."
    )
    steps = [
        "Inspect the transaction's line items: outliers usually trace to a "
        "single oversized item (premium add-on, one-off device charge) "
        "rather than a structural billing bug.",
        "Cross-check the customer's package change history — a mid-cycle "
        "upgrade legitimately produces a one-time spike that the rule "
        "can't distinguish from fraud.",
        "If the customer has fewer than ~15 prior cycles, the σ estimate "
        "is unstable — treat as needs_more_info rather than acting on it.",
    ]
    return line, steps


def _rule_narrative_velocity_anomaly(ev: dict) -> tuple[str, list[str]]:
    """Pipeline projects: txn_count, total_myr, first_at, last_at, txn_ids."""
    c = ev.get("txn_count") or ev.get("count") or 0
    total = ev.get("total_myr")
    ids = ev.get("txn_ids") or []
    ids_str = _list_str([_shorten_id(t) for t in ids], max_items=3)
    line = (
        f"{int(_num(c) or 0)} transactions in the velocity window totalling "
        f"{_fmt_myr(total)}{f' (e.g. {ids_str})' if ids_str else ''}. "
        f"Single-customer fire on this rule is rare; clustered fires usually "
        f"point at retry storms from a stuck payment terminal."
    )
    steps = [
        "Check the merchant/terminal id on the involved transactions — "
        "a single failing terminal retrying is the most common cause.",
        "Confirm the customer wasn't running an automated subscription "
        "renewal that fired mid-test (look for identical amounts).",
        "If the cluster is genuine (e.g. card-not-present testing), "
        "block the card and open a fraud investigation.",
    ]
    return line, steps


def _rule_narrative_geographic_anomaly(ev: dict) -> tuple[str, list[str]]:
    txn_state = ev.get("txn_state") or ev.get("state")
    home = ev.get("home_state") or ev.get("customer_state")
    km = _num(ev.get("geographic_distance_from_home_km"))
    km_str = f" ({km:.0f} km from home)" if km is not None else ""
    line = (
        f"Transaction recorded in `{txn_state or '?'}` while the customer's "
        f"`service_state` at billing was `{home or '?'}`{km_str}."
    )
    steps = [
        "Check the customer's travel pattern — frequent inter-state customers "
        "produce false positives on this rule by design.",
        "Look at the device fingerprint / IP geo from the transaction event; "
        "a mismatch between device geo and home state is a stronger fraud "
        "signal than the billing-state field alone.",
        "If genuinely fraudulent, freeze the account and step-up auth on "
        "all open sessions.",
    ]
    return line, steps


def _rule_narrative_duplicate_transaction(ev: dict) -> tuple[str, list[str]]:
    """Pipeline projects: dup_count, txn_ids, first_at, last_at, total_myr."""
    c = ev.get("dup_count") or ev.get("duplicate_count") or ev.get("count") or 0
    total = ev.get("total_myr")
    ids = ev.get("txn_ids") or []
    ids_str = _list_str([_shorten_id(t) for t in ids], max_items=4)
    line = (
        f"{int(_num(c) or 0)} duplicate transactions of {_fmt_myr(total)} each "
        f"on the same customer+merchant+amount within the window"
        f"{f': {ids_str}' if ids_str else ''}."
    )
    steps = [
        "Reverse all but the first transaction in the duplicate set — the "
        "downstream gateway almost always intends only one of them.",
        "Pull the gateway log around `first_at` → `last_at` to identify the "
        "retry trigger (timeout, 5xx response, network partition).",
        "Open a remediation ticket against the offending integration if the "
        "same retry pattern is appearing on other customers this week.",
    ]
    return line, steps


def _rule_narrative_termination_fee_check(ev: dict) -> tuple[str, list[str]]:
    fee = ev.get("charged_fee") or ev.get("amount") or ev.get("total_myr")
    expected = ev.get("expected_fee")
    lock_in = ev.get("is_lock_in_period")
    extras: list[str] = []
    if expected is not None:
        extras.append(f"expected {_fmt_myr(expected)}")
    if lock_in is True:
        extras.append("customer was still in lock-in")
    suffix = f" ({', '.join(extras)})" if extras else ""
    line = (
        f"Termination fee of {_fmt_myr(fee)} charged{suffix}. The rule fires "
        f"when either the fee diverges from the contractual amount or the "
        f"customer was still in their lock-in window."
    )
    steps = [
        "Pull the customer's contract start date and lock-in length; verify "
        "the billing timestamp falls outside lock-in.",
        "If the customer is genuinely still in lock-in but the cancellation "
        "is approved (relocation, hardship), waive the fee and document.",
        "If the fee amount is wrong, recompute against the pro-rata "
        "schedule and refund the delta.",
    ]
    return line, steps


def _rule_narrative_proration_check(ev: dict) -> tuple[str, list[str]]:
    billed = (
        ev.get("billed_amount")
        or ev.get("proration_amount_myr")
        or ev.get("actual_proration_myr")
    )
    expected = ev.get("expected_proration") or ev.get("expected_proration_myr")
    line = (
        f"Mid-cycle proration billed {_fmt_myr(billed)} vs expected "
        f"{_fmt_myr(expected)} — outside the configured tolerance band."
    )
    steps = [
        "Recompute the proration using the customer's effective change date "
        "and the package's days-in-cycle; small deltas almost always trace "
        "to a date-rounding edge case.",
        "If the customer changed plans more than once in the cycle, the "
        "rule's single-change assumption may not hold — escalate for manual "
        "review rather than auto-correcting.",
    ]
    return line, steps


def _rule_narrative_double_charge_multi_code(ev: dict) -> tuple[str, list[str]]:
    codes = ev.get("charge_codes") or []
    c = ev.get("txn_count") or 0
    codes_str = _list_str(codes, max_items=4) or "(codes not captured)"
    line = (
        f"Same logical service billed via {int(_num(c) or len(codes))} "
        f"distinct charge codes: {codes_str}. The redundant-code rule is "
        f"the highest-precision indicator we have for billing-system drift."
    )
    steps = [
        f"Reverse the redundant charge(s) on whichever code is non-canonical "
        f"(check the charge-code catalog for the `is_primary=true` entry).",
        "Open a config ticket against the source of the redundant code — "
        "leaving it live will keep generating these cases for every PPV "
        "customer in this window.",
    ]
    return line, steps


def _rule_narrative_unearned_earned_segregation(ev: dict) -> tuple[str, list[str]]:
    earned = ev.get("earned_amount_myr")
    unearned = ev.get("unearned_amount_myr")
    line_total = ev.get("line_total_myr") or ev.get("total_myr")
    line = (
        f"Subscription line carries earned={_fmt_myr(earned)} / "
        f"unearned={_fmt_myr(unearned)} against line total {_fmt_myr(line_total)} "
        f"— the split is missing or doesn't sum within tolerance."
    )
    steps = [
        "Recompute earned/unearned using the package's revenue-recognition "
        "schedule against the cycle's elapsed days.",
        "This rule almost never indicates customer-facing harm; route to "
        "revenue ops for the GL correction rather than treating it as a "
        "billing dispute.",
    ]
    return line, steps


# Dispatch table — keep in lockstep with `RULE_BUILDERS` in
# `app/pipelines/rule_pipeline_builders.py`.
_RULE_NARRATIVES: dict[str, Callable[[dict], tuple[str, list[str]]]] = {
    "discount_mismatch":           _rule_narrative_discount_mismatch,
    "entitlement_mismatch":        _rule_narrative_entitlement_mismatch,
    "amount_outlier":              _rule_narrative_amount_outlier,
    "velocity_anomaly":            _rule_narrative_velocity_anomaly,
    "geographic_anomaly":          _rule_narrative_geographic_anomaly,
    "duplicate_transaction":       _rule_narrative_duplicate_transaction,
    "termination_fee_check":       _rule_narrative_termination_fee_check,
    "proration_check":             _rule_narrative_proration_check,
    "double_charge_multi_code":    _rule_narrative_double_charge_multi_code,
    "unearned_earned_segregation": _rule_narrative_unearned_earned_segregation,
}


def _retrieval_grounded_likelihood(
    retrieval: list[dict],
    *,
    base_likelihood: str,
    base_confidence: float,
) -> tuple[str, float, str | None]:
    """Adjust likelihood/confidence using TP/FP counts from retrieved cases.

    Returns `(likelihood, confidence, narration)`. `narration` is a
    one-liner suitable for the rationale section when at least 2 cases
    were retrieved.
    """
    if not retrieval:
        return base_likelihood, base_confidence, None
    tp = sum(1 for s in retrieval if isinstance(s, dict) and s.get("disposition") == "true_positive")
    fp = sum(1 for s in retrieval if isinstance(s, dict) and s.get("disposition") == "false_positive")
    total = sum(
        1 for s in retrieval
        if isinstance(s, dict) and s.get("disposition") in ("true_positive", "false_positive")
    )
    if total < 2:
        return base_likelihood, base_confidence, None
    tp_rate = tp / total
    fp_rate = fp / total
    # Blend the prior (base_confidence) with the empirical rate so the
    # number doesn't swing wildly off a 2-case sample.
    weight = min(1.0, total / 5.0)
    if tp_rate >= 0.7:
        likelihood = "true_positive"
        confidence = round(base_confidence * (1 - weight) + 0.85 * weight, 2)
    elif fp_rate >= 0.7:
        likelihood = "false_positive"
        confidence = round(base_confidence * (1 - weight) + 0.75 * weight, 2)
    else:
        likelihood = "needs_more_info"
        confidence = round(base_confidence * (1 - weight) + 0.5 * weight, 2)
    narration = (
        f"Of {total} similar historical case(s) retrieved: "
        f"{tp} resolved as true_positive, {fp} as false_positive."
    )
    return likelihood, confidence, narration


def _customer_snapshot_signals(case: dict) -> list[str]:
    """Pull narrative signals from the case's frozen `customer_snapshot`.

    The live `customer` doc isn't always hydrated, but every V3 case
    carries this projection — we always have something to say about
    tenure / lock-in / package value.
    """
    snap = case.get("customer_snapshot") or {}
    out: list[str] = []
    tier = snap.get("tier") or snap.get("customer_segment")
    tenure = snap.get("tenure_months")
    pkg = snap.get("package_at_billing")
    churn = snap.get("churn_risk")
    state = snap.get("service_state")
    lifetime_q = snap.get("lifetime_quarantine_count")
    entitlements = snap.get("active_entitlements_at_billing") or []
    if pkg or tier:
        bits = []
        if tier:
            bits.append(str(tier))
        if pkg:
            bits.append(f"on `{pkg}`")
        if state:
            bits.append(f"in {state}")
        if tenure:
            bits.append(f"({int(tenure)}mo tenure)")
        out.append("Customer profile: " + " ".join(bits) + ".")
    if isinstance(lifetime_q, (int, float)) and lifetime_q >= 3:
        out.append(
            f"Repeat-offender signal: {int(lifetime_q)} prior quarantine "
            f"events on this account."
        )
    if isinstance(churn, str) and churn.lower() in ("high", "very_high"):
        out.append(
            f"Churn risk = `{churn}` — a wrong decision here has retention impact."
        )
    if isinstance(entitlements, list) and len(entitlements) > 0:
        out.append(
            f"{len(entitlements)} entitlement(s) active at billing "
            f"({_list_str(entitlements, max_items=2)})."
        )
    return out


def default_synthesizer(state: dict[str, Any]) -> dict:
    """Rule-based AiAssist composer.

    Always returns a valid `AiAssist`-shaped dict — never raises. When
    every upstream tool failed, `degraded=True` and `degraded_reason`
    enumerates the per-node errors.

    Compared to the original PR-AG baseline, this version:
      * leads `summary` with the strongest evidence value (amount, σ,
        missing entitlements) rather than just rule names;
      * generates per-rule-type rationale paragraphs that explain WHY
        the rule fired, grounded in the pipeline's projected evidence;
      * emits per-rule-type recommended_steps with concrete next
        actions an analyst can take without re-reading the rule code;
      * grounds `likelihood`/`confidence` in the retrieved-cases TP/FP
        distribution when ≥2 dispositions are present;
      * pulls customer narrative from `customer_snapshot` even when the
        live customer doc isn't hydrated.
    """
    case = state.get("case") or {}
    classification = state.get("classification") or {}
    analytics = state.get("analytics") or {}
    retrieval = state.get("retrieval") or []
    errors = state.get("errors") or []
    customer = state.get("customer") or {}
    transaction = state.get("transaction") or {}

    severity = case.get("severity") or "unknown"
    rules = case.get("rules_triggered") or []
    rule_types = sorted({
        r.get("rule_type")
        for r in rules
        if isinstance(r, dict) and r.get("rule_type")
    })
    revenue_impact = case.get("revenue_impact") or {}
    amount_at_risk = _num(revenue_impact.get("amount_at_risk_myr"))

    # ----- summary --------------------------------------------------
    # Lead with the dollar impact + headline rule narrative so the
    # analyst sees the case shape before any meta tags.
    summary_bits: list[str] = []
    if rule_types:
        amt_str = f"{_fmt_myr(amount_at_risk)} at risk · " if amount_at_risk else ""
        summary_bits.append(
            f"{amt_str}{severity} severity · "
            f"{len(rules)} rule(s): {', '.join(rule_types)}"
        )
        headline_rule = rules[0] if isinstance(rules[0], dict) else {}
        headline_type = headline_rule.get("rule_type")
        headline_ev = headline_rule.get("evidence") or {}
        narrator = _RULE_NARRATIVES.get(headline_type or "")
        if narrator:
            headline_line, _ = narrator(headline_ev if isinstance(headline_ev, dict) else {})
            summary_bits.append(headline_line)
    else:
        summary_bits.append(f"{severity} severity case with no rules fired")

    # ----- rationale (rich, multi-section) --------------------------
    rationale: list[str] = []

    # 1. Per-rule narratives (one bullet per rule type fired).
    rule_steps_collected: list[str] = []
    seen_types: set[str] = set()
    for r in rules:
        if not isinstance(r, dict):
            continue
        rtype = r.get("rule_type")
        if not rtype or rtype in seen_types:
            continue
        seen_types.add(rtype)
        narrator = _RULE_NARRATIVES.get(rtype)
        if not narrator:
            continue
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        line, steps = narrator(ev or {})
        rationale.append(line)
        rule_steps_collected.extend(steps)

    # 2. Classification confidence framing (only when meaningful).
    if classification.get("evidence_strength") == "strong":
        rationale.append(
            "Classifier marked evidence as `strong` (at least one rule "
            "reported confidence ≥ 0.8)."
        )

    # 3. Analytics signals — only when they carry content.
    pattern = analytics.get("customer_pattern") or {}
    if isinstance(pattern, dict) and "error" not in pattern:
        p_count = _num(pattern.get("txn_count")) or 0
        p_avg = _num(pattern.get("avg_amount")) or 0
        if p_count or p_avg:
            rationale.append(
                f"Customer 30-day pattern: {int(p_count)} txns, "
                f"avg {_fmt_myr(p_avg)}."
            )
    rule_freq = analytics.get("rule_type_frequency") or {}
    if isinstance(rule_freq, dict) and "error" not in rule_freq and "skipped" not in rule_freq:
        rf_count = _num(rule_freq.get("count")) or 0
        if rf_count:
            distinct = int(_num(rule_freq.get("distinct_customers")) or 0)
            scope = (
                "system-wide pattern"
                if distinct >= 5
                else "looks customer-specific"
            )
            rationale.append(
                f"Same rule_type fired {int(rf_count)} time(s) in the last "
                f"week across {distinct} customer(s) — {scope}."
            )
    drift = analytics.get("drift_snapshot") or {}
    if isinstance(drift, dict):
        snaps = drift.get("snapshots") or []
        drifted = [
            s for s in snaps if isinstance(s, dict) and s.get("drift_detected")
        ]
        if drifted:
            rationale.append(
                f"Upstream drift on {len(drifted)} feature(s): "
                f"{', '.join(s.get('feature_name', '?') for s in drifted)} "
                f"— the rule may be reacting to a model-input shift "
                f"rather than genuine fraud."
            )

    # 4. Customer snapshot (always available from the case doc).
    rationale.extend(_customer_snapshot_signals(case))

    # 5. Live customer/transaction hydration (when load_live_state ran).
    open_cases = customer.get("open_cases") or []
    if isinstance(open_cases, list) and len(open_cases) >= 2:
        rationale.append(
            f"Customer currently has {len(open_cases)} other open case(s); "
            f"check the related-cases panel for a shared root cause."
        )
    monthly = _num(customer.get("total_monthly_value_myr"))
    if monthly:
        rationale.append(
            f"Customer's recurring monthly value: {_fmt_myr(monthly)} "
            f"(retention impact framing)."
        )
    txn_items = transaction.get("items") or transaction.get("items_summary") or []
    if isinstance(txn_items, list) and txn_items:
        codes = [
            it.get("charge_code") for it in txn_items
            if isinstance(it, dict) and it.get("charge_code")
        ]
        codes_str = _list_str(codes, max_items=4)
        if codes_str:
            rationale.append(
                f"Transaction line items hit charge codes: {codes_str}."
            )
    discounts_applied = transaction.get("discounts_applied") or []
    if isinstance(discounts_applied, list) and discounts_applied:
        promo_ids = [
            d.get("promo_id") or d.get("discount_id")
            for d in discounts_applied
            if isinstance(d, dict)
        ]
        promo_ids = [p for p in promo_ids if p]
        if promo_ids:
            rationale.append(
                f"Discounts applied at billing: {_list_str(promo_ids, 5)} — "
                f"validate each promo's eligibility window."
            )

    # 6. Retrieval-grounded likelihood narration (added below where
    #    likelihood is computed, so analysts see the rate driving the call).
    if classification.get("evidence_strength") == "strong":
        base_likelihood, base_confidence = "true_positive", 0.78
    elif classification.get("evidence_strength") == "moderate":
        base_likelihood, base_confidence = "needs_more_info", 0.6
    else:
        base_likelihood, base_confidence = "needs_more_info", 0.3
    likelihood, confidence, hist_narration = _retrieval_grounded_likelihood(
        [s for s in retrieval if isinstance(s, dict)],
        base_likelihood=base_likelihood,
        base_confidence=base_confidence,
    )
    if hist_narration:
        rationale.append(hist_narration)

    if not rationale:
        rationale = [
            "No analytics signal available; treat this run as a "
            "deterministic fallback and review rule evidence manually."
        ]

    # ----- recommended steps ----------------------------------------
    # Order: severity escalation → rule-specific steps → cross-cutting
    # (retrieval hints, related-case checks) → catch-all.
    recommended_steps: list[str] = []
    if classification.get("complexity") == "high":
        recommended_steps.append(
            "Escalate to senior analyst — multi-rule high-severity case "
            "shouldn't be auto-dispositioned."
        )
    # Dedupe per-rule steps while preserving order.
    seen_steps: set[str] = set()
    for s in rule_steps_collected:
        if s in seen_steps:
            continue
        seen_steps.add(s)
        recommended_steps.append(s)
    # Retrieval-driven step.
    if any(
        isinstance(s, dict) and s.get("disposition") == "true_positive"
        for s in retrieval
    ):
        recommended_steps.append(
            "Similar historical cases resolved as true_positive — once "
            "evidence is confirmed, default to issuing a refund/correction "
            "rather than escalating further."
        )
    if any(
        isinstance(s, dict) and s.get("disposition") == "false_positive"
        for s in retrieval
    ):
        recommended_steps.append(
            "Historical neighbours include false_positive resolutions — "
            "look for the legitimate-but-unusual pattern (promo override, "
            "approved manual adjustment) before processing as fraud."
        )
    if isinstance(open_cases, list) and len(open_cases) >= 2:
        recommended_steps.append(
            "Cross-reference the customer's other open cases — disposing of "
            "the whole batch together is usually cheaper than one-at-a-time."
        )
    if not recommended_steps:
        recommended_steps.append(
            "Review rule evidence manually — no automated next-step "
            "candidates available."
        )

    # ----- references -----------------------------------------------
    references = [
        {
            "case_id": str(s.get("case_id")),
            "disposition": str(s.get("disposition") or "unknown"),
            "score": s.get("score"),
            "why_relevant": s.get("why_relevant"),
        }
        for s in retrieval
        if isinstance(s, dict) and s.get("case_id")
    ]

    degraded = bool(errors)

    return {
        "summary": " — ".join(summary_bits),
        "likelihood": likelihood,
        "confidence": confidence,
        "rationale": rationale,
        "recommended_steps": recommended_steps,
        "references": references,
        "degraded": degraded,
        "degraded_reason": "; ".join(errors) if degraded else None,
    }
