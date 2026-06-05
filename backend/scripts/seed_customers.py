"""Seed residential customers in the V3 flat-root shape.

Writes to `customers_residential` and mirrors a routing row into
`customer_index` for every seeded customer. The document shape satisfies
`ResidentialCustomerDocument` (Pydantic) and `CUSTOMER_RESIDENTIAL_VALIDATOR`
(JSON Schema) — flat-root identity scalars + embedded `contact`/`address`
blocks + the rich V3 sub-blocks (`entity_profiles`, `cross_entity_metrics`,
`recommendations`, `equipment`, `current_cycle`). PR-15 retired
`unified_profile`, `segment`, and the legacy manual-vector embedding
placeholders.

Each customer carries an `embed_source.text` leaf composed by
`build_customer_embed_text` — a deterministic flat sentence summarising
identity, packages, entities, churn / LTV signals, recent support and
recommendations. Atlas Auto Embedding indexes that string into the
customers AutoEmbed vector index so analysts can run natural-language
`$vectorSearch` queries ("find me a platinum customer in Selangor with
strong PPV velocity") through `/api/customers/search` — same pattern as
the `quarantine_cases_history` RAG corpus.

Public entrypoint matches the orchestrator contract:

    async def seed_customers(db, *, count: int = 10_000, batch_size: int = 1_000) -> int

Idempotency: full delete-then-insert on `customers_residential`; targeted
`{"customer_type": "residential"}` delete on `customer_index` (so the
commercial seeder's rows survive a residential-only re-seed).

Determinism: seeded with random.Random(42) + Faker.seed(42).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from faker import Faker

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS_RESIDENTIAL,
    SCHEMA_VERSION_V3,
)
from app.core.logging import get_logger
from app.services.embed_text_builder import build_customer_embed_text
from scripts._packages import PACKAGES
from scripts._realism import (
    pick_addon,
    pick_ppv_title,
    pick_promotion_for,
    pick_support_category,
    render_offer,
    render_support_narrative,
)

logger = get_logger(__name__)

fake = Faker("en_GB")
Faker.seed(42)
_rng = random.Random(42)

# Tier and weights (canonical customer classification — segment retired in PR-15).
TIERS: list[str] = ["bronze", "silver", "gold", "platinum"]
TIER_WEIGHTS: list[float] = [0.15, 0.45, 0.30, 0.10]

# Service / address pool.
STATES: list[str] = [
    "Selangor", "Kuala Lumpur", "Johor", "Penang", "Perak",
    "Sabah", "Sarawak", "Negeri Sembilan", "Kedah", "Pahang",
]
SERVICE_ZONES: dict[str, str] = {
    "Selangor":         "ZONE_KLG",
    "Kuala Lumpur":     "ZONE_KL",
    "Johor":            "ZONE_JHR",
    "Penang":           "ZONE_PEN",
    "Perak":            "ZONE_PRK",
    "Sabah":            "ZONE_SBH",
    "Sarawak":          "ZONE_SWK",
    "Negeri Sembilan":  "ZONE_NSN",
    "Kedah":            "ZONE_KDH",
    "Pahang":           "ZONE_PHG",
}
# Approximate state centroids (longitude, latitude) for a sensible
# GeoJSONPoint. The vector index doesn't need precision; the validator
# only checks the structure.
STATE_GEO: dict[str, tuple[float, float]] = {
    "Selangor":         (101.5183, 3.0738),
    "Kuala Lumpur":     (101.6869, 3.1390),
    "Johor":            (103.7414, 1.4854),
    "Penang":           (100.3327, 5.4164),
    "Perak":            (101.0901, 4.5921),
    "Sabah":            (116.0735, 5.9788),
    "Sarawak":          (113.0438, 2.5574),
    "Negeri Sembilan":  (102.0978, 2.7297),
    "Kedah":            (100.7700, 6.1184),
    "Pahang":           (102.5413, 3.8126),
}

# Acme service entities for entity_profiles keyed lookups.
ENTITY_PAYTV = "acme_paytv"
ENTITY_STREAMING = "acme_streaming"
STREAMING_SERVICES = ("acme_on_demand", "sooka", "disney_plus_hotstar")

GENRES = ["sports", "movies", "drama", "news", "kids"]


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pick_tier(rng: random.Random) -> str:
    return rng.choices(TIERS, weights=TIER_WEIGHTS, k=1)[0]


def _ic_number(rng: random.Random) -> str:
    """Synthetic Malaysian IC: YYMMDD-PB-NNNN."""
    yy = rng.randint(0, 99)
    mm = rng.randint(1, 12)
    dd = rng.randint(1, 28)
    pb = rng.randint(1, 16)
    nnnn = rng.randint(1000, 9999)
    return f"{yy:02d}{mm:02d}{dd:02d}-{pb:02d}-{nnnn:04d}"


def _pick_packages(rng: random.Random, tier: str) -> list[tuple[str, str, float, str]]:
    if tier == "bronze":
        pool = [p for p in PACKAGES if p[3] == "value"]
        n = 1
    elif tier == "silver":
        pool = [p for p in PACKAGES if p[3] in {"value", "standard"}]
        n = rng.randint(1, 2)
    elif tier == "gold":
        pool = [p for p in PACKAGES if p[3] in {"standard", "premium"}]
        n = rng.randint(1, 3)
    else:  # platinum
        pool = [p for p in PACKAGES if p[3] in {"standard", "premium"}]
        n = rng.randint(2, 3)
    return rng.sample(pool, k=min(n, len(pool)))


def _build_address(rng: random.Random, *, state: str) -> dict:
    """V3 address with GeoJSON Point + service_zone + moved_in_at."""
    lon, lat = STATE_GEO[state]
    return {
        "line1":    fake.street_address(),
        "line2":    None,
        "city":     fake.city(),
        "state":    state,
        "postcode": fake.postcode(),
        "country":  "MY",
        "location": {
            "type":        "Point",
            "coordinates": [
                lon + rng.uniform(-0.2, 0.2),
                lat + rng.uniform(-0.2, 0.2),
            ],
        },
        "service_zone": SERVICE_ZONES[state],
        "moved_in_at":  _utcnow() - timedelta(days=rng.randint(180, 3650)),
    }


def _build_contact(rng: random.Random) -> dict:
    return {
        "email":     fake.unique.email(),
        "phone":     f"+60-1{rng.randint(0, 9)}-{rng.randint(1_000_000, 9_999_999)}",
        "alt_phone": None,
        "channel_opt_ins": [
            {
                "channel":        "email",
                "opted_in":       True,
                "opted_in_date":  _utcnow() - timedelta(days=rng.randint(30, 900)),
            }
        ],
        "channel_opt_outs": [],
        "communication_preferences": {
            "preferred_language":      "en",
            "secondary_language":      rng.choice(["ms", "zh", None]),
            "quiet_hours_start":       None,
            "quiet_hours_end":         None,
            "preferred_contact_time":  None,
            "do_not_disturb":          False,
            "billing_email_format":    "html",
            "marketing_frequency_cap_per_week": rng.choice([2, 3, 5, None]),
        },
    }


def _build_identity_scalars(rng: random.Random, *, name: str) -> dict:
    """Flat-root identity scalars (preferred_name, gender, household_size, etc.)."""
    return {
        "preferred_name":  name.split()[0] if rng.random() < 0.5 else None,
        "ethnicity":       None,
        "date_of_birth":   None,
        "gender":          rng.choice(["male", "female"]),
        "marital_status":  rng.choice(["single", "married"]),
        "household_size":  rng.randint(1, 6),
        "occupation_band": None,
    }


def _build_subscriptions(
    rng: random.Random, packages: list[tuple[str, str, float, str]], started: datetime
) -> list[dict]:
    out: list[dict] = []
    for p in packages:
        sub_started = started + timedelta(days=rng.randint(0, 30))
        out.append({
            "package_code":     p[0],
            "package_name":     p[1],
            "status":           "active",
            "monthly_fee_myr":  p[2],
            "started_at":       sub_started,
            "next_billing_at":  sub_started + timedelta(days=30),
        })
    return out


def _build_promotions(
    rng: random.Random,
    *,
    now: datetime,
    tier: str,
    tenure_months: int,
) -> list[dict]:
    if rng.random() >= 0.30:
        return []
    code, description, amount, _date_range = pick_promotion_for(
        rng, tier=tier, tenure_months=tenure_months,
    )
    return [{
        "promotion_code":      code,
        "description":         description,
        "discount_pct":        None,
        "discount_amount_myr": amount,
        "valid_from":          now - timedelta(days=rng.randint(1, 60)),
        "valid_to":             now + timedelta(days=rng.randint(7, 90)),
    }]


def _build_entitlements(rng: random.Random, *, now: datetime) -> list[dict]:
    """Entitlements are realistic Acme PPV / addon entries.

    A mix of PPV titles (sports, concerts, movies) and OTT add-ons —
    the same pools the transactions seeder pulls from so cross-references
    stay coherent.
    """
    out: list[dict] = []
    for _ in range(rng.randint(0, 4)):
        if rng.random() < 0.7:
            content_id, content_name, _price, _category = pick_ppv_title(rng)
        else:
            addon_code, addon_name, _partner, _price = pick_addon(rng)
            content_id, content_name = addon_code, addon_name
        out.append({
            "content_id":    content_id,
            "content_name":  content_name,
            "granted_at":    now - timedelta(days=rng.randint(1, 60)),
            "expires_at":    now + timedelta(days=rng.randint(7, 180)),
        })
    return out


def _build_recent_support(
    rng: random.Random,
    *,
    now: datetime,
    tier: str,
    state: str,
    churn_band: str,
) -> list[dict]:
    """Pick realistic Acme support narratives by tier × churn band."""
    out: list[dict] = []
    for _ in range(rng.randint(0, 3)):
        category, subcategory = pick_support_category(
            rng, tier=tier, churn_band=churn_band,
        )
        ticket_num = f"{rng.randint(10000, 99999)}"
        ctx = {
            "tier":           tier,
            "state":          state,
            "month":          now.strftime("%B %Y"),
            "amount":         round(rng.uniform(15.0, 95.0), 2),
            "promo_name":     "Hari Raya Aidilfitri",
            "promo_code":     "PROMO_RAYA_2026",
            "valid_to":       "2026-06-30",
            "ticket_num":     ticket_num,
            "points":         rng.randint(120, 4500),
            "redeemed":       rng.randint(50, 800),
            "outlet1":        "Pelita KLCC",
            "outlet2":        "Pelita Bukit Bintang",
            "quote_total":    round(rng.uniform(1200.0, 9500.0), 2),
            "decoder_serial": f"{rng.randint(100000, 999999)}",
            "firmware":       f"ULTRA-{rng.randint(7,9)}.{rng.randint(0,4)}.{rng.randint(0,9)}",
            "channel_pack":   "Acme Family Pack HD + Sports Pass",
        }
        summary, _full_notes = render_support_narrative(
            rng, category=category, subcategory=subcategory, context=ctx,
        )
        opened = now - timedelta(days=rng.randint(1, 90))
        out.append({
            "ticket_id":  f"tkt_{uuid.uuid4().hex[:10]}",
            "summary":    summary,
            "opened_at":  opened,
            "closed_at":  opened + timedelta(hours=rng.randint(1, 96)),
            "sentiment":  rng.choices(
                ["positive", "neutral", "negative"], weights=[0.45, 0.40, 0.15], k=1
            )[0],
        })
    return out


def _build_entity_profiles(
    rng: random.Random, *, packages: list[tuple[str, str, float, str]], started: datetime
) -> tuple[list[str], dict]:
    """Pick entities the customer subscribes to + their per-service blocks."""
    entities: list[str] = [ENTITY_PAYTV]
    if rng.random() < 0.55:
        entities.append(ENTITY_STREAMING)

    profiles: dict[str, dict] = {}
    primary = packages[0]
    profiles[ENTITY_PAYTV] = {
        "member_since":             started,
        "primary_package_code":     primary[0],
        "primary_package_name":     primary[1],
        "monthly_fee_myr":          primary[2],
        "decoder_count":            rng.randint(1, 3),
        "viewing_hours_30d":        round(rng.uniform(5.0, 180.0), 1),
        "favorite_genres":          rng.sample(GENRES, k=rng.randint(1, 3)),
        "lock_in_until":            started + timedelta(days=rng.randint(180, 720)),
        "remaining_lock_in_months": rng.randint(0, 24),
    }
    if ENTITY_STREAMING in entities:
        profiles[ENTITY_STREAMING] = {
            "member_since":              started + timedelta(days=rng.randint(0, 365)),
            "service":                   rng.choice(STREAMING_SERVICES),
            "monthly_fee_myr":           round(rng.uniform(15.0, 50.0), 2),
            "watch_time_30d_minutes":    round(rng.uniform(100.0, 5000.0), 1),
            "favorite_apps":             rng.sample(["sooka", "ondemand", "iqiyi"], k=rng.randint(1, 2)),
            "concurrent_streams_used_p95": rng.randint(1, 3),
        }
    return entities, profiles


def _ltv_band(total: float) -> str:
    if total < 2000:
        return "low"
    if total < 8000:
        return "medium"
    if total < 20000:
        return "high"
    return "very_high"


def _churn_band(score: float) -> str:
    if score < 0.25:
        return "low"
    if score < 0.55:
        return "medium"
    if score < 0.80:
        return "high"
    return "very_high"


def _cross_sell_band(score: float) -> str:
    if score < 0.34:
        return "low"
    if score < 0.67:
        return "medium"
    return "high"


def _trend_12m(
    rng: random.Random, *, base: float, monetary: bool, jitter: float = 0.25
) -> list[dict]:
    """12 calendar months of `{month, value_myr|value}` ending at the
    current month. Uses calendar-month arithmetic so we never produce
    duplicate months across long sequences (28/30/31-day drift)."""
    cursor = _utcnow().replace(day=1)
    out: list[dict] = []
    for i in range(12):
        # Walk back 11..0 months.
        target = cursor - relativedelta(months=11 - i)
        month = f"{target.year:04d}-{target.month:02d}"
        v = max(0.0, base * rng.uniform(1 - jitter, 1 + jitter))
        if monetary:
            out.append({"month": month, "value_myr": round(v, 2)})
        else:
            out.append({"month": month, "value": round(v, 1)})
    return out


def _build_cross_entity_metrics(
    rng: random.Random, *, tier: str, monthly_value: float, tenure_months: int
) -> dict:
    base_total_ltv = monthly_value * max(tenure_months, 1)
    cross_sell_score = round(
        min(1.0, max(0.0, rng.uniform(0.1, 0.9) + (0.15 if tier in {"gold", "platinum"} else 0))),
        3,
    )
    churn = round(
        min(1.0, max(0.0, rng.uniform(0.1, 0.9) - (0.20 if tier == "platinum" else 0)
                          + (0.15 if tier == "bronze" else 0))),
        3,
    )
    engagement = round(rng.uniform(0.2, 0.95), 3)
    return {
        "total_ltv_myr":        round(base_total_ltv, 2),
        "ltv_band":             _ltv_band(base_total_ltv),
        "cross_sell_score":     cross_sell_score,
        "cross_sell_band":      _cross_sell_band(cross_sell_score),
        "churn_risk":           churn,
        "churn_risk_band":      _churn_band(churn),
        "engagement_index":     engagement,
        "ltv_trend_12m":        _trend_12m(rng, base=monthly_value, monetary=True),
        "monthly_spend_trend_12m": _trend_12m(rng, base=monthly_value, monetary=True),
        "viewing_hours_trend_12m": _trend_12m(rng, base=80.0, monetary=False),
        "ppv_count_trend_12m":     _trend_12m(rng, base=4.0, monetary=False),
    }


def _build_brand_journey(rng: random.Random, *, started: datetime) -> list[dict]:
    """Realistic brand-journey events with per-event detail blocks."""
    out: list[dict] = []
    for _ in range(rng.randint(1, 3)):
        event = rng.choice([
            "signup", "package_upgrade", "package_downgrade",
            "ppv_purchase", "support_ticket", "campaign_response",
        ])
        details: dict = {}
        if event == "signup":
            details = {"channel": rng.choice(["online", "agent", "retail_partner"])}
        elif event == "package_upgrade":
            details = {
                "from_package": "Acme Family Pack SD",
                "to_package":   "Acme Family Pack HD",
                "price_delta_myr": 50.00,
            }
        elif event == "package_downgrade":
            details = {
                "from_package": "Acme Sports Plus",
                "to_package":   "Acme Family Pack HD",
                "price_delta_myr": -19.95,
            }
        elif event == "ppv_purchase":
            ppv = pick_ppv_title(rng)
            details = {"content_id": ppv[0], "title": ppv[1], "price_myr": ppv[2]}
        elif event == "support_ticket":
            details = {
                "category": rng.choice(["billing", "technical", "loyalty"]),
                "channel":  rng.choice(["phone", "chat", "whatsapp"]),
            }
        elif event == "campaign_response":
            details = {
                "campaign_id": f"camp_{rng.randint(1000, 9999)}",
                "channel":     rng.choice(["email", "sms", "whatsapp"]),
                "outcome":     rng.choice(["clicked", "converted", "ignored"]),
            }
        out.append({
            "entity":   rng.choice([ENTITY_PAYTV, ENTITY_STREAMING]),
            "event":    event,
            "date":     started + timedelta(days=rng.randint(0, 1500)),
            "details":  details,
        })
    return out


def _build_interaction_history(
    rng: random.Random,
    *,
    now: datetime,
    tier: str,
    state: str,
    churn_band: str,
) -> dict:
    support: list[dict] = []
    for _ in range(rng.randint(0, 2)):
        d = now - timedelta(days=rng.randint(1, 180))
        category, subcategory = pick_support_category(
            rng, tier=tier, churn_band=churn_band,
        )
        ticket_num = f"{rng.randint(10000, 99999)}"
        ctx = {
            "tier":           tier,
            "state":          state,
            "month":          d.strftime("%B %Y"),
            "amount":         round(rng.uniform(15.0, 95.0), 2),
            "promo_name":     "Hari Raya Aidilfitri",
            "promo_code":     "PROMO_RAYA_2026",
            "valid_to":       "2026-06-30",
            "ticket_num":     ticket_num,
            "points":         rng.randint(120, 4500),
            "redeemed":       rng.randint(50, 800),
            "outlet1":        "Pelita KLCC",
            "outlet2":        "Pelita Bukit Bintang",
            "quote_total":    round(rng.uniform(1200.0, 9500.0), 2),
            "decoder_serial": f"{rng.randint(100000, 999999)}",
            "firmware":       f"ULTRA-{rng.randint(7,9)}.{rng.randint(0,4)}.{rng.randint(0,9)}",
            "channel_pack":   "Acme Family Pack HD + Sports Pass",
        }
        _summary, full_notes = render_support_narrative(
            rng, category=category, subcategory=subcategory, context=ctx,
        )
        support.append({
            "ticket_id":               f"tkt_h_{uuid.uuid4().hex[:10]}",
            "date":                    d,
            "channel":                 rng.choice(["phone", "chat", "email"]),
            "agent_id":                f"agent_{rng.randint(1, 30):02d}",
            "category":                category,
            "subcategory":             subcategory,
            "sentiment":               rng.choice(["positive", "neutral", "negative"]),
            "resolution":              rng.choice(["resolved", "escalated", "pending"]),
            "resolution_time_minutes": rng.randint(5, 120),
            "notes":                   full_notes,
        })

    marketing: list[dict] = []
    for _ in range(rng.randint(0, 3)):
        sent = now - timedelta(days=rng.randint(1, 120))
        opened = sent + timedelta(hours=rng.randint(1, 72)) if rng.random() < 0.6 else None
        clicked = (
            opened + timedelta(minutes=rng.randint(1, 30))
            if opened is not None and rng.random() < 0.4
            else None
        )
        marketing.append({
            "campaign_id":             f"camp_{rng.randint(1000, 9999)}",
            "content_id":              f"cnt_{rng.randint(100, 999)}",
            "channel":                 rng.choice(["email", "sms", "whatsapp"]),
            "sent_at":                 sent,
            "opened_at":               opened,
            "clicked_at":              clicked,
            "converted_at":            None,
            "revenue_attributed_myr":  0.0,
        })

    return {
        "support_interactions":   support,
        "marketing_interactions": marketing,
        "channel_engagement_rates": {
            "email": {
                "open_rate":         round(rng.uniform(0.05, 0.45), 3),
                "ctr":               round(rng.uniform(0.01, 0.12), 3),
                "conversion_rate":   round(rng.uniform(0.0, 0.05), 3),
                "total_sent":        rng.randint(5, 50),
                "last_engaged_at":   now - timedelta(days=rng.randint(1, 60)),
            }
        },
    }


def _build_active_campaigns(rng: random.Random, *, now: datetime) -> list[dict]:
    if rng.random() >= 0.30:
        return []
    return [{
        "campaign_id":                     f"camp_{rng.randint(1000, 9999)}",
        "campaign_name":                   "Win-Back Q2",
        "enrollment_id":                   f"enr_{uuid.uuid4().hex[:10]}",
        "enrolled_date":                   now - timedelta(days=rng.randint(1, 30)),
        "enrolled_by":                     "ml_signal",
        "signal_id":                       None,
        "content_asset_id":                None,
        "content_headline":                None,
        "recommended_channel":             "email",
        "reasoning":                       None,
        "similar_customer_conversion_rate": round(rng.uniform(0.05, 0.30), 3),
        "expected_ltv_uplift_myr":         round(rng.uniform(20.0, 200.0), 2),
        "similar_customers_sampled":       [],
        "status":                          "in_flight",
        "scheduled_send_at":               now + timedelta(days=rng.randint(1, 14)),
        "last_status_change_at":           now,
    }]


_CHURN_DRIVERS = [
    "rising_support_volume",
    "declining_viewing_hours",
    "no_recent_promo_engagement",
    "discount_dependence",
    "delayed_payments",
    "competitor_streaming_growth",
]
_OFFER_TYPES = ["upgrade", "addon", "retention_discount", "winback", "loyalty_perk"]


def _build_recommendations(
    rng: random.Random,
    *,
    churn_score: float,
    tier: str,
    entities: list[str],
    monthly_spend_trend: str = "rising",
) -> dict:
    band = "low" if churn_score < 0.34 else ("medium" if churn_score < 0.67 else "high")
    drivers = rng.sample(_CHURN_DRIVERS, k=rng.randint(1, 3))
    n_offers = rng.randint(0, 2)
    offers: list[dict] = []
    for i in range(n_offers):
        otype = rng.choice(_OFFER_TYPES)
        title, rationale = render_offer(
            rng,
            offer_type=otype,
            tier=tier,
            drivers=drivers,
            monthly_spend_trend=monthly_spend_trend,
            entities=entities,
        )
        offers.append({
            "offer_id":          f"offer_{uuid.uuid4().hex[:10]}",
            "offer_type":        otype,
            "title":             title,
            "rationale":         rationale,
            "expected_uplift_myr": round(rng.uniform(10.0, 120.0), 2),
            "priority":          rng.randint(1, 10),
        })
    return {
        "computed_at":  _utcnow(),
        "churn_risk": {
            "band":    band,
            "score":   round(churn_score, 3),
            "drivers": drivers,
        },
        "next_best_offers": offers,
    }


def _build_equipment(rng: random.Random, *, now: datetime) -> list[dict]:
    out: list[dict] = []
    n = rng.randint(1, 3)
    for i in range(n):
        kind = "set_top_box" if i == 0 else rng.choice(["smartcard", "router", "remote_control"])
        installed = now - timedelta(days=rng.randint(60, 1500))
        item = {
            "equipment_id":         f"eq_{uuid.uuid4().hex[:10]}",
            "type":                 kind,
            "model":                rng.choice(["U2", "BYOD", "ULTRA_4K", "STREAM_v3"]),
            "serial":               f"SN-{rng.randint(10**6, 10**7 - 1)}",
            "smart_card":           f"SC-{rng.randint(10**6, 10**7 - 1)}" if kind == "set_top_box" else None,
            "status":               "active",
            "installed_at":         installed,
            "last_seen_at":         now - timedelta(hours=rng.randint(0, 96)),
            "firmware_version":     f"v{rng.randint(1, 9)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}",
            "location_in_premises": rng.choice(["living_room", "bedroom", "study"]),
        }
        out.append(item)
    return out


def _build_current_cycle(rng: random.Random, *, monthly_value: float, now: datetime) -> dict:
    cycle_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cycle_end = (cycle_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
    days_remaining = max(0, (cycle_end - now).days)
    return {
        "cycle_id":              f"cyc_{cycle_start.strftime('%Y%m')}_{uuid.uuid4().hex[:6]}",
        "cycle_start":           cycle_start,
        "cycle_end":             cycle_end,
        "expected_amount_myr":   round(monthly_value, 2),
        "billed_amount_myr":     None,
        "transactions_to_date":  0,
        "days_remaining":        days_remaining,
    }


def _build_customer(rng: random.Random, idx: int) -> tuple[dict, dict]:
    """Return (customer_doc, customer_index_row) — V3 flat-root shape."""
    cid = f"cust_{idx:06d}"
    account_id = f"ACC_RES_{idx:06d}"
    name = fake.name()
    ic = _ic_number(rng)
    state = rng.choice(STATES)
    tier = _pick_tier(rng)
    now = _utcnow()
    started = now - timedelta(days=rng.randint(30, 1500))
    tenure_months = max(1, (now - started).days // 30)

    address = _build_address(rng, state=state)
    contact = _build_contact(rng)
    identity = _build_identity_scalars(rng, name=name)
    packages = _pick_packages(rng, tier)
    subscriptions = _build_subscriptions(rng, packages, started)
    monthly_value = sum(s["monthly_fee_myr"] for s in subscriptions)

    entities, entity_profiles = _build_entity_profiles(rng, packages=packages, started=started)
    cross_metrics = _build_cross_entity_metrics(
        rng, tier=tier, monthly_value=monthly_value, tenure_months=tenure_months
    )
    churn_band = cross_metrics["churn_risk_band"]
    spend_trend_pts = cross_metrics["monthly_spend_trend_12m"]
    spend_trend_label = (
        "rising"
        if spend_trend_pts and spend_trend_pts[-1].get("value_myr", 0)
        > spend_trend_pts[0].get("value_myr", 0)
        else "flat"
    )
    promotions = _build_promotions(rng, now=now, tier=tier, tenure_months=tenure_months)
    entitlements = _build_entitlements(rng, now=now)
    support = _build_recent_support(
        rng, now=now, tier=tier, state=state, churn_band=churn_band,
    )
    recommendations = _build_recommendations(
        rng,
        churn_score=cross_metrics["churn_risk"],
        tier=tier,
        entities=entities,
        monthly_spend_trend=spend_trend_label,
    )

    doc: dict = {
        "_schema_version":     SCHEMA_VERSION_V3,
        "customer_id":         cid,
        "customer_type":       "residential",
        "account_id":          account_id,
        "parent_account_id":   None,
        "tier":                tier,
        # Identity scalars (lifted to root in PR-15)
        "name":                name,
        "ic_number":           ic,
        **identity,
        # Embedded blocks (root-level)
        "contact":             contact,
        "address":             address,
        # Hot-path embeds
        "subscriptions":       subscriptions,
        "active_promotions":   promotions,
        "entitlements":        entitlements,
        "recent_transactions": [],
        "recent_support":      support,
        "open_cases":          [],
        "latest_features":     None,
        # Aggregates
        "total_monthly_value_myr":   round(monthly_value, 2),
        "lifetime_quarantine_count": 0,
        # Rich V3 sub-blocks
        "entities":             entities,
        "entity_profiles":      entity_profiles,
        "cross_entity_metrics": cross_metrics,
        "brand_journey":        _build_brand_journey(rng, started=started),
        "interaction_history":  _build_interaction_history(
            rng, now=now, tier=tier, state=state, churn_band=churn_band,
        ),
        "active_campaigns":     _build_active_campaigns(rng, now=now),
        "recommendations":      recommendations,
        "equipment":            _build_equipment(rng, now=now),
        "current_cycle":        _build_current_cycle(rng, monthly_value=monthly_value, now=now),
        "created_at":           started,
        "updated_at":           now,
    }

    # AutoEmbed payload — Atlas reads `embed_source.text` via the
    # customers AutoEmbed index and stores the vector invisibly. Composed
    # last so the builder sees the full document.
    doc["embed_source"] = {"text": build_customer_embed_text(doc)}

    index_row: dict = {
        "_schema_version":     SCHEMA_VERSION_V3,
        "customer_id":         cid,
        "customer_type":       "residential",
        "account_id":          account_id,
        "parent_account_id":   None,
        "name":                name,
        "outlet_label":        None,
        "updated_at":          now,
    }
    return doc, index_row


# -------------------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------------------

async def seed_customers(
    db, *, count: int = 10_000, batch_size: int = 1_000
) -> int:
    """Seed `count` residential customers (V3 shape) + customer_index rows.

    Returns the number of inserted customer documents. Idempotent —
    deletes the entire `customers_residential` collection and the
    residential-typed rows of `customer_index` before re-seeding.
    """
    coll = db[CUSTOMERS_RESIDENTIAL]
    coll_idx = db[CUSTOMER_INDEX]

    await coll.delete_many({})
    await coll_idx.delete_many({"customer_type": "residential"})

    rng = random.Random(42)
    written = 0
    pending_docs: list[dict] = []
    pending_idx: list[dict] = []
    for i in range(count):
        doc, idx_row = _build_customer(rng, i)
        pending_docs.append(doc)
        pending_idx.append(idx_row)
        if len(pending_docs) >= batch_size:
            await coll.insert_many(pending_docs, ordered=False)
            await coll_idx.insert_many(pending_idx, ordered=False)
            written += len(pending_docs)
            pending_docs.clear()
            pending_idx.clear()
            logger.info("customers_progress", written=written, target=count)

    if pending_docs:
        await coll.insert_many(pending_docs, ordered=False)
        await coll_idx.insert_many(pending_idx, ordered=False)
        written += len(pending_docs)

    logger.info("customers_seeded", count=written)
    return written
