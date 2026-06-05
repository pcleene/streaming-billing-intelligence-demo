"""Unit tests for `build_customer_embed_text`.

The customer AutoEmbed flow (analogous to the `quarantine_cases_history`
RAG corpus) writes a deterministic, flat string to `embed_source.text`
on each customer document. Atlas then embeds that string in-cluster and
serves the vector via the customers AutoEmbed indexes. Verifies that
the builder:

  * surfaces the high-signal facets (tier, location, entities, packages,
    LTV / churn / engagement, recommendations, support narrative);
  * stays tolerant of partial / commercial documents;
  * never blows up on missing scalars.
"""

from __future__ import annotations

from app.services.embed_text_builder import build_customer_embed_text


def _rich_residential() -> dict:
    return {
        "customer_id": "cust_R_001",
        "customer_type": "residential",
        "tier": "platinum",
        "name": "Aisha Rahim",
        "address": {"state": "Selangor", "city": "Petaling Jaya"},
        "entities": ["acme_paytv", "acme_streaming"],
        "subscriptions": [
            {
                "package_code": "AFH",
                "package_name": "Acme Family HD",
                "monthly_fee_myr": 124.55,
            },
            {
                "package_code": "ASP",
                "package_name": "Acme Sports Plus",
                "monthly_fee_myr": 45.00,
            },
        ],
        "entitlements": [
            {"content_id": "ppv_epl_01", "content_name": "EPL Liverpool vs Arsenal"},
        ],
        "active_promotions": [
            {"promotion_code": "PROMO_RAYA", "description": "Hari Raya RM30"},
        ],
        "total_monthly_value_myr": 169.55,
        "cross_entity_metrics": {
            "total_ltv_myr": 9500.0,
            "ltv_band": "high",
            "churn_risk": 0.32,
            "churn_risk_band": "medium",
            "engagement_index": 0.72,
            "cross_sell_band": "medium",
        },
        "recommendations": {
            "churn_risk": {
                "band": "medium",
                "drivers": ["rising_support_volume", "discount_dependence"],
            },
            "next_best_offers": [
                {"title": "Upgrade to Acme Ultra Box"},
            ],
        },
        "recent_support": [
            {"summary": "Billing question about missing Hari Raya rebate. Resolved."},
        ],
        "household_size": 4,
        "gender": "male",
        "marital_status": "married",
    }


def _minimal_commercial() -> dict:
    return {
        "customer_id": "cust_C_outlet_1",
        "customer_type": "commercial",
        "tier": "gold",
        "name": "Mamak Corner Sdn Bhd",
        "address": {"state": "Penang"},
        "entities": ["acme_business"],
        "subscriptions": [
            {"package_code": "BIZ_SPORTS", "monthly_fee_myr": 699.0},
        ],
        "cross_entity_metrics": {
            "total_ltv_myr": 12000.0,
            "churn_risk": 0.15,
            "churn_risk_band": "low",
        },
        "business_profile": {
            "industry": "F&B",
            "business_type": "Restaurant",
            "outlet_label": "Mamak Corner — KL Sentral",
            "venue_capacity": 80,
        },
    }


def test_residential_text_surfaces_core_signals() -> None:
    text = build_customer_embed_text(_rich_residential())

    # Identity & geography
    assert "platinum" in text.lower()
    assert "Aisha Rahim" in text
    assert "Selangor" in text
    assert "Petaling Jaya" in text

    # Entities (with the acme_ prefix stripped for readability)
    assert "paytv" in text
    assert "streaming" in text

    # Packages
    assert "Acme Family HD" in text
    assert "124.55" in text

    # Promotions + entitlements
    assert "Hari Raya RM30" in text
    assert "EPL Liverpool vs Arsenal" in text

    # Financial / churn / engagement signals
    assert "9500.00" in text
    assert "churn risk medium" in text
    assert "engagement 0.72" in text
    assert "cross-sell band medium" in text

    # Recommendations narrative
    assert "rising_support_volume" in text
    assert "Upgrade to Acme Ultra Box" in text

    # Recent support narrative
    assert "Billing question about missing Hari Raya rebate" in text

    # Identity scalars are deliberately last so they don't dominate
    # cosine similarity for non-demographic queries.
    assert text.rfind("Identity:") > text.rfind("Recent support:")


def test_commercial_text_surfaces_business_profile() -> None:
    text = build_customer_embed_text(_minimal_commercial())

    assert "gold" in text.lower()
    assert "Mamak Corner Sdn Bhd" in text
    assert "industry F&B" in text
    assert "Restaurant" in text
    assert "outlet Mamak Corner — KL Sentral" in text
    assert "capacity 80" in text
    # No PPV entitlements / promotions on this minimal doc — must not
    # blow up the builder.
    assert "Entitlements" not in text
    assert "Active promotions" not in text


def test_handles_empty_document_gracefully() -> None:
    text = build_customer_embed_text({})

    # The builder still emits a sentence with placeholder values rather
    # than raising, so the seed scripts never have to special-case it.
    assert text
    assert "customer" in text.lower()
    # Default location fallback when no address is supplied.
    assert "Malaysia" in text


def test_partial_recommendations_dont_break_summary() -> None:
    doc = {
        "customer_id": "cust_R_002",
        "customer_type": "residential",
        "tier": "silver",
        "name": "Test Customer",
        "recommendations": {"churn_risk": {"band": "high"}},
        # No next_best_offers, no drivers.
    }
    text = build_customer_embed_text(doc)
    assert "Recommendations: churn band high" in text
    # Make sure the missing fields didn't produce stray separators.
    assert "; ;" not in text
    assert "drivers:" not in text
