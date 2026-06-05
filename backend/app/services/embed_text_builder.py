"""Pure text builders for the AutoEmbed `embed_source.text` leaf.

The Atlas Auto Embedding pipeline (ADR-032) embeds whatever string sits at
`embed_source.text` and stores the vector invisibly. The application no
longer calls Voyage directly; it only writes this string. The builders in
this module are the canonical place where each `embed_source.text`
payload is composed.

Scope:
  * `build_history_embed_text`  — RAG corpus on `quarantine_cases_history`
  * `build_customer_embed_text` — semantic customer search on
    `customers_residential` / `customers_commercial`. Composed from the
    customer's identity, packages, entities, geography, churn & LTV
    signals, recent support interactions and recommendations so analysts
    can phrase "find me customers like…" queries in plain English.

Contract
--------
* Pure: no DB, no network, no Voyage SDK calls.
* Deterministic: given the same input, returns the same string.
* Self-contained: tolerant of missing or partial keys (so the builder
  works on lean PR-1 / PR-3 docs as well as the rich V3 / V4 shapes).
* Output ≥ 200 characters in normal usage. Very thin inputs surface a
  ``thin_embed_source`` warning (caller logs it); the function still
  returns the best string it can rather than raising — corpus quality
  is monitored via the realism gates in §7.5 of the migration prompt.

The composition reuses the existing fragment helpers in
:mod:`app.services.embedding_service` so the legacy code path
(``EmbeddingService.embed_text`` against the manual-vector indexes) and
the AutoEmbed path produce identical text. PR-C strips the legacy path
and inlines the helpers here.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.embedding_service import EmbeddingService

logger = get_logger(__name__)

# Length floor for the `embed_source.text` leaf. Documents whose builder
# output falls below this floor surface a structured warning so seed
# gaps and thin operational docs don't pass silently.
EMBED_TEXT_MIN_CHARS = 200


def _warn_if_thin(kind: str, identifier: str | None, text: str) -> str:
    if len(text) < EMBED_TEXT_MIN_CHARS:
        logger.warning(
            "thin_embed_source",
            kind=kind,
            identifier=identifier,
            length=len(text),
            floor=EMBED_TEXT_MIN_CHARS,
        )
    return text


def build_history_embed_text(history: dict[str, Any]) -> str:
    """Build the `embed_source.text` payload for a resolved case.

    Composed from disposition, severity, rules + their evidence,
    transaction summary, customer context, resolution summary, analyst
    notes, and learnings. Used by `case_history_repo` on archive and by
    `scripts/seed_history.py` at write time.
    """
    text = EmbeddingService.history_to_embedding_text(history)
    return _warn_if_thin(
        kind="history",
        identifier=history.get("case_id") or history.get("id"),
        text=text,
    )


# ---------------------------------------------------------------------
# Customer embed source (semantic "find me customers like…" search)
# ---------------------------------------------------------------------

def _fmt_amount(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _join_short(items: list[str], *, sep: str = ", ", cap: int = 6) -> str:
    cleaned = [s for s in items if s]
    if not cleaned:
        return ""
    return sep.join(cleaned[:cap])


def _summarise_packages(subs: list[dict[str, Any]] | None) -> str:
    if not subs:
        return ""
    out: list[str] = []
    for s in subs[:4]:
        if not isinstance(s, dict):
            continue
        name = s.get("package_name") or s.get("package_code") or "?"
        fee = _fmt_amount(s.get("monthly_fee_myr"))
        out.append(f"{name} @{fee} MYR/mo")
    return "; ".join(out)


def _summarise_entitlements(entitlements: list[dict[str, Any]] | None) -> str:
    if not entitlements:
        return ""
    names: list[str] = []
    for e in entitlements[:6]:
        if not isinstance(e, dict):
            continue
        nm = e.get("content_name") or e.get("content_id")
        if nm:
            names.append(str(nm))
    return _join_short(names)


def _summarise_recommendations(recs: dict[str, Any] | None) -> str:
    if not recs:
        return ""
    chunks: list[str] = []
    churn = recs.get("churn_risk") or {}
    if isinstance(churn, dict):
        band = churn.get("band")
        drivers = churn.get("drivers") or []
        if band:
            chunks.append(f"churn band {band}")
        if drivers:
            chunks.append("drivers: " + _join_short(drivers, cap=4))
    offers = recs.get("next_best_offers") or []
    titles = [o.get("title") for o in offers if isinstance(o, dict) and o.get("title")]
    if titles:
        chunks.append("next-best-offers: " + _join_short(titles, sep=" / ", cap=3))
    return "; ".join(c for c in chunks if c)


def _summarise_recent_support(items: list[dict[str, Any]] | None) -> str:
    if not items:
        return ""
    summaries: list[str] = []
    for it in items[:3]:
        if not isinstance(it, dict):
            continue
        sm = it.get("summary")
        if sm:
            summaries.append(str(sm))
    return " | ".join(summaries)


def _summarise_business_profile(bp: dict[str, Any] | None) -> str:
    if not bp:
        return ""
    parts: list[str] = []
    industry = bp.get("industry")
    biz_type = bp.get("business_type")
    outlet_label = bp.get("outlet_label")
    capacity = bp.get("venue_capacity")
    if industry:
        parts.append(f"industry {industry}")
    if biz_type:
        parts.append(f"type {biz_type}")
    if outlet_label:
        parts.append(f"outlet {outlet_label}")
    if capacity:
        parts.append(f"capacity {capacity}")
    return ", ".join(parts)


def build_customer_embed_text(customer: dict[str, Any]) -> str:
    """Build the `embed_source.text` payload for a customer document.

    Composes a deterministic flat-string summary of the customer's
    identity, geography, packages, entities, lifetime/churn signals,
    recent support narrative and active offers. The Atlas AutoEmbed
    index reads this string and stores the vector invisibly so analysts
    can run natural-language `$vectorSearch` queries ("platinum
    customer with strong PPV velocity in Selangor") through the customer
    search endpoint.

    Tolerant of partial inputs — works on both V3 residential and
    commercial documents.
    """
    ctype = customer.get("customer_type") or "customer"
    tier = customer.get("tier") or "unknown"
    name = customer.get("name") or customer.get("customer_id") or "anonymous"
    state = (customer.get("address") or {}).get("state")
    city = (customer.get("address") or {}).get("city")

    entities = customer.get("entities") or []
    entity_text = _join_short(
        [str(e).replace("acme_", "") for e in entities], cap=8
    )

    packages_text = _summarise_packages(customer.get("subscriptions"))
    entitlements_text = _summarise_entitlements(customer.get("entitlements"))

    monthly = _fmt_amount(customer.get("total_monthly_value_myr"))
    cem = customer.get("cross_entity_metrics") or {}
    ltv = _fmt_amount(cem.get("total_ltv_myr"))
    ltv_band = cem.get("ltv_band") or "unknown"
    churn_score = cem.get("churn_risk")
    churn_band = cem.get("churn_risk_band") or "unknown"
    engagement = cem.get("engagement_index")
    cross_sell_band = cem.get("cross_sell_band") or "unknown"

    rec_text = _summarise_recommendations(customer.get("recommendations"))
    support_text = _summarise_recent_support(customer.get("recent_support"))

    promos = customer.get("active_promotions") or []
    promo_names = [
        p.get("description") or p.get("promotion_code")
        for p in promos if isinstance(p, dict)
    ]
    promo_text = _join_short([str(p) for p in promo_names if p], cap=3)

    biz_text = _summarise_business_profile(customer.get("business_profile"))

    location_bits = [b for b in (city, state) if b]
    location_text = ", ".join(location_bits) if location_bits else "Malaysia"

    parts: list[str] = [
        f"{ctype.capitalize()} {tier}-tier Acme customer {name} in {location_text}.",
    ]
    if entity_text:
        parts.append(f"Subscribes to: {entity_text}.")
    if packages_text:
        parts.append(f"Active packages: {packages_text}.")
    if entitlements_text:
        parts.append(f"Entitlements: {entitlements_text}.")
    if promo_text:
        parts.append(f"Active promotions: {promo_text}.")
    if biz_text:
        parts.append(f"Business profile: {biz_text}.")

    spend_chunks: list[str] = [
        f"monthly spend {monthly} MYR",
        f"total LTV {ltv} MYR ({ltv_band})",
        f"churn risk {churn_band}"
        + (f" ({float(churn_score):.2f})" if isinstance(churn_score, (int, float)) else ""),
        f"cross-sell band {cross_sell_band}",
    ]
    if isinstance(engagement, (int, float)):
        spend_chunks.append(f"engagement {float(engagement):.2f}")
    parts.append("Signals: " + ", ".join(spend_chunks) + ".")

    if rec_text:
        parts.append(f"Recommendations: {rec_text}.")
    if support_text:
        parts.append(f"Recent support: {support_text}")

    # Identity scalars are written last so they don't dominate the
    # cosine space for natural-language queries that don't mention them.
    identity_bits: list[str] = []
    household = customer.get("household_size")
    if isinstance(household, int) and household > 0:
        identity_bits.append(f"household_size={household}")
    if customer.get("gender"):
        identity_bits.append(f"gender={customer['gender']}")
    if customer.get("marital_status"):
        identity_bits.append(f"marital_status={customer['marital_status']}")
    if identity_bits:
        parts.append("Identity: " + ", ".join(identity_bits) + ".")

    text = " ".join(p for p in parts if p).strip()
    return _warn_if_thin(
        kind="customer",
        identifier=customer.get("customer_id"),
        text=text,
    )
