"""Seed commercial parents + outlets in the V3 flat-root shape.

Writes to `customers_commercial` and mirrors a routing row into
`customer_index` for every parent and outlet. The document shape satisfies
`CommercialCustomerDocument` (Pydantic) and `CUSTOMER_COMMERCIAL_VALIDATOR`
(JSON Schema): top-level `customer_id`, `name`, `tier`, `contact`, plus the
rich V3 blocks (`business_profile`, `entity_profiles`, `cross_entity_metrics`,
`recommendations`, `equipment`, `current_cycle`). PR-15 retired
`unified_profile`, `segment`, the legacy `commercial_profile` alias, the
top-level `business_registration_no` mirror, and the legacy manual-vector
embedding placeholders.

Each parent / outlet doc carries an `embed_source.text` leaf composed by
`build_customer_embed_text`. The Atlas Auto Embedding index on this
collection embeds that string and stores the vector invisibly so the
semantic customer-search endpoint can rank commercial outlets alongside
residential customers with a single `$vectorSearch` query (same pattern
as the `quarantine_cases_history` RAG corpus).

Public entrypoint matches the orchestrator contract in `scripts/seed.py`:

    async def main(db, *, parent_count: int = 5,
                   outlets_per_parent_min: int = 2,
                   outlets_per_parent_max: int = 4) -> dict

Idempotency: `_seed_marker` delete-by-marker on both `customers_commercial`
and `customer_index` so re-runs don't double-insert and the residential
seeder's index rows survive.

Determinism: random.Random(37) + Faker.seed(37) (distinct from residential
seed 42 to avoid name collisions in index search).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from faker import Faker

from app.core.constants import (
    CUSTOMER_INDEX,
    CUSTOMERS_COMMERCIAL,
    SCHEMA_VERSION_V3,
)
from app.core.logging import get_logger
from app.services.embed_text_builder import build_customer_embed_text
from scripts._realism import (
    COMMERCIAL_BUSINESSES,
    KL_VENUES,
    pick_addon,
    pick_package,
    pick_ppv_title,
    pick_promotion_for,
    render_offer,
)

logger = get_logger(__name__)

_SEED_MARKER = "pr14"

fake = Faker("en_GB")
Faker.seed(37)

# --- Pools ---------------------------------------------------------------

# Tier is the canonical customer classification (segment retired in PR-15).
# Commercial customers tend to skew gold/platinum.
_TIERS: tuple[str, ...] = ("gold", "platinum")

# Business types per CommercialProfile schema.
_BUSINESS_TYPES: tuple[str, ...] = (
    "sdn_bhd", "berhad", "sole_proprietor", "partnership", "other",
)
# Industries — feed CommercialProfile.industry.
_INDUSTRIES: tuple[str, ...] = (
    "hospitality", "retail", "office", "f_and_b", "fitness",
    "healthcare", "education",
)
# Mapping industry -> typical name suffix to make seeded data feel real.
_INDUSTRY_SUFFIX: dict[str, str] = {
    "hospitality": "Hotel",
    "retail":      "Mart",
    "office":      "Holdings",
    "f_and_b":     "Cafe",
    "fitness":     "Gym",
    "healthcare":  "Clinic",
    "education":   "Academy",
}

# Map COMMERCIAL_BUSINESSES.business_type strings to our internal industry enum.
_REALISM_BIZTYPE_TO_INDUSTRY: dict[str, str] = {
    "restaurant": "f_and_b",
    "cafe":       "f_and_b",
    "bar":        "f_and_b",
    "hotel":      "hospitality",
    "gym":        "fitness",
    "retail":     "retail",
    "cinema":     "hospitality",
}

_STATES: tuple[str, ...] = (
    "Selangor", "Kuala Lumpur", "Johor", "Penang", "Perak",
    "Sabah", "Sarawak", "Negeri Sembilan", "Kedah", "Pahang",
)
_SERVICE_ZONES: dict[str, str] = {
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
_STATE_GEO: dict[str, tuple[float, float]] = {
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
_ENTITY_BIZ = "acme_biz"
_ENTITY_BIZ_STREAMING = "acme_biz_streaming"


# --- Time helpers --------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _months_ago(rng: random.Random, lo: int, hi: int) -> datetime:
    return _utcnow() - timedelta(days=rng.randint(lo * 30, hi * 30))


# --- Address / profile builders -----------------------------------------

def _build_v3_address(rng: random.Random, state: str) -> dict:
    lon, lat = _STATE_GEO[state]
    # Jitter the centroid slightly so nearby outlets aren't all stacked.
    lon += (rng.random() - 0.5) * 0.20
    lat += (rng.random() - 0.5) * 0.20
    return {
        "line1":      f"Lot {rng.randint(1, 999)}, Jalan Acme Biz",
        "line2":      f"Block {rng.choice(list('ABCDEFGH'))}",
        "city":       fake.city(),
        "state":      state,
        "postcode":   f"{rng.randint(10000, 99999)}",
        "country":    "MY",
        "location":   {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
        "service_zone": _SERVICE_ZONES[state],
        "moved_in_at":  _months_ago(rng, 6, 60),
    }


def _build_contact(rng: random.Random, *, idx_seed: int) -> dict:
    """Root-level contact block (account-manager email/phone)."""
    return {
        "email": (
            f"ops{idx_seed:02d}@acmebiz-{idx_seed:02d}.example.my"
        ),
        "phone": (
            f"+60-{rng.randint(10, 19)}-{rng.randint(1000000, 9999999)}"
        ),
        "alt_phone": None,
        "channel_opt_ins": [
            {
                "channel": "email",
                "opted_in": True,
                "opted_in_date": _months_ago(rng, 6, 36),
            },
            {
                "channel": "account_manager",
                "opted_in": True,
                "opted_in_date": _months_ago(rng, 6, 36),
            },
        ],
        "channel_opt_outs": ["telemarketing"],
        "communication_preferences": {
            "preferred_language": "en",
            "secondary_language": "ms",
            "quiet_hours_start":  "22:00",
            "quiet_hours_end":    "08:00",
            "preferred_contact_time": "business_hours",
            "do_not_disturb": False,
            "billing_email_format": "pdf",
            "marketing_frequency_cap_per_week": 2,
        },
    }


# --- Subscription / promotion / entitlement builders --------------------

def _pick_packages(
    rng: random.Random, *, max_n: int,
) -> list[tuple[str, str, float, str, str]]:
    """Pick 1..max_n distinct commercial packages from the realism catalog."""
    seen_codes: set[str] = set()
    out: list[tuple[str, str, float, str, str]] = []
    n = rng.randint(1, max_n)
    attempts = 0
    while len(out) < n and attempts < 32:
        attempts += 1
        pkg = pick_package(rng, segment="commercial")
        if pkg[0] in seen_codes:
            continue
        seen_codes.add(pkg[0])
        out.append(pkg)
    return out


def _build_subscriptions(
    rng: random.Random,
    picks: list[tuple[str, str, float, str, str]],
) -> list[dict]:
    out: list[dict] = []
    for code, pname, fee, _segment, _target_tier in picks:
        started = _months_ago(rng, 6, 36)
        out.append({
            "package_code":     code,
            "package_name":     pname,
            "status":           "active",
            "monthly_fee_myr":  fee,
            "started_at":       started,
            "next_billing_at":  started + timedelta(days=30),
        })
    return out


def _build_promotions(
    rng: random.Random, *, tier: str, tenure_months: int,
) -> list[dict]:
    if rng.random() >= 0.6:
        return []
    code, description, amount_myr, (valid_from_iso, valid_to_iso) = pick_promotion_for(
        rng, tier=tier, tenure_months=tenure_months,
    )
    valid_from = datetime.fromisoformat(valid_from_iso).replace(tzinfo=timezone.utc)
    valid_to = datetime.fromisoformat(valid_to_iso).replace(tzinfo=timezone.utc)
    return [{
        "promotion_code":      code,
        "description":         description,
        "discount_pct":        None,
        "discount_amount_myr": amount_myr,
        "valid_from":          valid_from,
        "valid_to":            valid_to,
    }]


def _build_entitlements(rng: random.Random) -> list[dict]:
    """Pick 1-3 commercial-relevant PPV entitlements (sports-leaning)."""
    n = rng.randint(1, 3)
    seen: set[str] = set()
    out: list[dict] = []
    for _ in range(n * 2):
        if len(out) >= n:
            break
        # Commercial venues skew to live_sports / esports for crowd appeal.
        category = rng.choice(["live_sports", "live_sports", "esports", "concert"])
        content_id, title, _price, _cat = pick_ppv_title(rng, category=category)
        if content_id in seen:
            continue
        seen.add(content_id)
        granted = _utcnow() - timedelta(days=rng.randint(1, 60))
        out.append({
            "content_id":   content_id,
            "content_name": title,
            "granted_at":   granted,
            "expires_at":   granted + timedelta(days=365),
        })
    return out


# --- Entity profiles (business) -----------------------------------------

def _build_entity_profiles_business(
    rng: random.Random,
    *,
    primary_pkg: tuple,
    outlet_count: int | None,
    contract_terms: dict | None,
    parent_customer_id: str | None,
) -> dict:
    pcode, pname, fee = primary_pkg[0], primary_pkg[1], primary_pkg[2]
    biz_block = {
        "member_since":            _months_ago(rng, 12, 60),
        "primary_package_code":    pcode,
        "primary_package_name":    pname,
        "monthly_fee_myr":         fee,
        "no_of_outlets":           outlet_count,
        "contract_terms":          contract_terms,
        "package_inherited_from":  parent_customer_id,
        "viewing_hours_30d_avg_per_screen": round(rng.uniform(40.0, 220.0), 1),
        "screen_count":            rng.randint(1, 8),
    }
    profiles = {_ENTITY_BIZ: biz_block}
    if rng.random() < 0.5:
        profiles[_ENTITY_BIZ_STREAMING] = {
            "member_since":              _months_ago(rng, 6, 36),
            "service":                   "acme_on_demand",
            "monthly_fee_myr":           rng.choice([29.0, 49.0, 79.0]),
            "watch_time_30d_minutes":    round(rng.uniform(120.0, 4_000.0), 0),
            "favorite_apps":             rng.sample(
                ["sooka", "netflix", "iqiyi", "viu"], k=rng.randint(1, 3)
            ),
            "concurrent_streams_used_p95": rng.randint(1, 4),
        }
    return profiles


# --- Contract terms -----------------------------------------------------

def _build_contract_terms(rng: random.Random) -> dict:
    start = _months_ago(rng, 6, 36)
    months = rng.choice([12, 24, 36])
    end = start + timedelta(days=months * 30)
    remaining = max(0, (end - _utcnow()).days // 30)
    return {
        "contract_start":             start,
        "contract_end":               end,
        "auto_renew":                 True,
        "early_termination_fee_myr":  round(rng.uniform(500.0, 3_500.0), 2),
        "remaining_months":           remaining,
    }


# --- Cross-entity metrics ------------------------------------------------

def _ltv_band(ltv: float) -> str:
    if ltv >= 50_000:
        return "very_high"
    if ltv >= 25_000:
        return "high"
    if ltv >= 8_000:
        return "medium"
    return "low"


def _churn_band(score: float) -> str:
    if score >= 0.75:
        return "very_high"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _cross_sell_band(score: float) -> str:
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "medium"
    return "low"


def _trend_12m(rng: random.Random, *, base: float, jitter: float) -> list[dict]:
    """12 calendar months ending at the previous month — uses calendar-
    month arithmetic so we never produce duplicates due to 28/30/31-day
    drift over long sequences."""
    cursor = _utcnow().replace(day=1)
    out: list[dict] = []
    for i in range(12, 0, -1):
        d = cursor - relativedelta(months=i)
        v = max(0.0, base + rng.uniform(-jitter, jitter))
        out.append({
            "month":     d.strftime("%Y-%m"),
            "value_myr": round(v, 2),
            "value":     None,
        })
    return out


def _build_cross_entity_metrics(
    rng: random.Random, *, monthly_value: float, outlet_dist: list[dict] | None,
) -> dict:
    ltv = round(monthly_value * rng.randint(18, 36), 2)
    churn = round(rng.uniform(0.05, 0.55), 3)
    cross = round(rng.uniform(0.1, 0.8), 3)
    return {
        "total_ltv_myr":       ltv,
        "ltv_band":            _ltv_band(ltv),
        "cross_sell_score":    cross,
        "cross_sell_band":     _cross_sell_band(cross),
        "churn_risk":          churn,
        "churn_risk_band":     _churn_band(churn),
        "engagement_index":    round(rng.uniform(0.2, 0.95), 3),
        "ltv_trend_12m":       _trend_12m(rng, base=monthly_value, jitter=monthly_value * 0.15),
        "monthly_spend_trend_12m": _trend_12m(rng, base=monthly_value, jitter=monthly_value * 0.10),
        "viewing_hours_trend_12m": None,
        "ppv_count_trend_12m":     None,
        "outlet_revenue_distribution_30d": outlet_dist,
    }


# --- Recommendations / equipment / current cycle ------------------------

def _build_recommendations(
    rng: random.Random,
    *,
    churn: float,
    tier: str,
    entities: list[str],
    monthly_spend_trend: str | None = None,
) -> dict:
    drivers = rng.sample(
        ["lock_in_expiring_soon", "low_engagement",
         "competitor_promo_window", "support_ticket_unresolved"],
        k=rng.randint(1, 3),
    )
    offer_type = "retention_discount" if churn >= 0.5 else "upgrade"
    title, rationale = render_offer(
        rng,
        offer_type=offer_type,
        tier=tier,
        drivers=drivers,
        monthly_spend_trend=monthly_spend_trend,
        entities=entities,
    )
    return {
        "computed_at": _utcnow(),
        "churn_risk": {
            "band":    _churn_band(churn) if churn < 0.75 else "high",
            "score":   churn if churn <= 1.0 else 1.0,
            "drivers": drivers,
        },
        "next_best_offers": [{
            "offer_id":            f"nbo_{uuid.uuid4().hex[:8]}",
            "offer_type":          offer_type,
            "title":               title,
            "rationale":           rationale,
            "expected_uplift_myr": round(rng.uniform(50.0, 400.0), 2),
            "priority":            rng.randint(1, 5),
        }],
    }


def _build_equipment(rng: random.Random, *, count: int) -> list[dict]:
    out: list[dict] = []
    for k in range(count):
        installed = _months_ago(rng, 1, 36)
        out.append({
            "equipment_id":   f"eqp_{uuid.uuid4().hex[:10]}",
            "type":           rng.choice(["set_top_box", "smartcard", "router"]),
            "model":          rng.choice(["Ultra Box X1", "Ultra Box X2", "Smart Hub Pro"]),
            "serial":         f"SN{rng.randint(100000000, 999999999)}",
            "smart_card":     f"SC{rng.randint(100000, 999999)}",
            "status":         "active",
            "installed_at":   installed,
            "last_seen_at":   _utcnow() - timedelta(hours=rng.randint(1, 72)),
            "firmware_version": f"3.{rng.randint(0, 9)}.{rng.randint(0, 30)}",
            "location_in_premises": rng.choice(
                ["main_lobby", "bar_area", "private_room", "back_office"]
            ),
        })
    return out


def _build_current_cycle(rng: random.Random, *, expected: float) -> dict:
    cycle_start = _utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_start = (cycle_start + timedelta(days=32)).replace(day=1)
    cycle_end = next_start - timedelta(seconds=1)
    return {
        "cycle_id":            f"CYC_{cycle_start.strftime('%Y%m')}_{uuid.uuid4().hex[:6]}",
        "cycle_start":         cycle_start,
        "cycle_end":           cycle_end,
        "expected_amount_myr": round(expected, 2),
        "billed_amount_myr":   None,
        "transactions_to_date": rng.randint(0, 60),
        "days_remaining":      max(0, (cycle_end - _utcnow()).days),
    }


# --- Index row -----------------------------------------------------------

def _index_doc(doc: dict) -> dict:
    """Build the `customer_index` row for a customer doc (V3 shape)."""
    return {
        "_schema_version":   SCHEMA_VERSION_V3,
        "customer_id":       doc["customer_id"],
        "customer_type":     doc["customer_type"],
        "account_id":        doc["account_id"],
        "parent_account_id": doc.get("parent_account_id"),
        "outlet_label":      doc.get("business_profile", {}).get("outlet_label"),
        "name":              doc["name"],
        "updated_at":        _utcnow(),
        "_seed_marker":      _SEED_MARKER,
    }


# --- Document builders --------------------------------------------------

def _new_account_id(prefix: str, idx: int, sub: int | None = None) -> str:
    if sub is None:
        return f"ACC_{prefix}_{idx:02d}"
    return f"ACC_{prefix}_{idx:02d}_{sub:02d}"


def _spend_trend_label(trend: list[dict]) -> str:
    """Coarse rising/flat/falling label from the 12-month series."""
    if len(trend) < 2:
        return "flat"
    first = trend[0]["value_myr"]
    last = trend[-1]["value_myr"]
    if first == 0:
        return "flat"
    delta_pct = (last - first) / first
    if delta_pct >= 0.05:
        return "rising"
    if delta_pct <= -0.05:
        return "falling"
    return "flat"


def _build_parent(
    rng: random.Random, idx: int, outlet_count: int,
) -> dict:
    cid = f"comm_parent_{idx:02d}"
    account_id = _new_account_id("PAR", idx)
    state = rng.choice(_STATES)

    # Pull a curated Acme-flavoured business; cycle on idx so each parent
    # gets a distinct realistic name + SSM.
    realism_biz = COMMERCIAL_BUSINESSES[(idx - 1) % len(COMMERCIAL_BUSINESSES)]
    name, realism_biztype, ssm_no, _outlet_labels = realism_biz
    industry = _REALISM_BIZTYPE_TO_INDUSTRY.get(realism_biztype, "f_and_b")
    biz_reg = ssm_no
    business_type = "sdn_bhd" if name.endswith("Sdn Bhd") else "berhad"

    tier = rng.choice(_TIERS)
    tenure_months = rng.randint(12, 60)

    picks = _pick_packages(rng, max_n=2)
    primary = picks[0]
    subscriptions = _build_subscriptions(rng, picks)
    monthly_value = sum(s["monthly_fee_myr"] for s in subscriptions)

    contract_terms = _build_contract_terms(rng)
    registered_address = _build_v3_address(rng, state)
    contact_address = _build_v3_address(rng, state)
    contact = _build_contact(rng, idx_seed=idx)

    business_profile = {
        "business_name":          name,
        "business_registration_no": biz_reg,
        "business_type":          business_type,
        "industry":               industry,
        "venue_capacity":         rng.randint(50, 500),
        "outlet_label":           None,  # parent
        "registered_address":     registered_address,
        "contract_terms":         contract_terms,
    }

    entity_profiles = _build_entity_profiles_business(
        rng,
        primary_pkg=primary,
        outlet_count=outlet_count,
        contract_terms=contract_terms,
        parent_customer_id=None,
    )
    entities = list(entity_profiles.keys())

    cem = _build_cross_entity_metrics(
        rng,
        monthly_value=monthly_value,
        outlet_dist=None,  # filled after outlets are built
    )
    churn = cem["churn_risk"]
    spend_trend_label = _spend_trend_label(cem["monthly_spend_trend_12m"])
    recommendations = _build_recommendations(
        rng,
        churn=churn,
        tier=tier,
        entities=entities,
        monthly_spend_trend=spend_trend_label,
    )

    doc = {
        # Top-level (validator-required).
        "_schema_version":     SCHEMA_VERSION_V3,
        "customer_id":         cid,
        "name":                name,
        "customer_type":       "commercial",
        "account_id":          account_id,
        "parent_account_id":   None,
        "outlet_id":           None,
        "tier":                tier,

        # Identity scalars (lifted to root in PR-15)
        "preferred_name":  None,
        "ic_number":       None,
        "date_of_birth":   None,
        "ethnicity":       None,
        "gender":          None,
        "marital_status":  None,
        "household_size":  None,
        "occupation_band": None,

        # Embedded blocks (root-level)
        "contact":             contact,
        "address":             contact_address,

        # V3 rich blocks
        "business_profile":    business_profile,
        "entities":            entities,
        "entity_profiles":     entity_profiles,
        "cross_entity_metrics": cem,
        "brand_journey":       [],
        "interaction_history": {
            "support_interactions":    [],
            "marketing_interactions":  [],
            "channel_engagement_rates": {},
        },
        "active_campaigns":    [],
        "recommendations":     recommendations,

        "equipment":           _build_equipment(rng, count=rng.randint(2, 5)),
        "current_cycle":       _build_current_cycle(rng, expected=monthly_value),

        # Hot-path embeds.
        "subscriptions":       subscriptions,
        "active_promotions":   _build_promotions(
            rng, tier=tier, tenure_months=tenure_months,
        ),
        "entitlements":        _build_entitlements(rng),
        "recent_transactions": [],
        "open_cases":          [],
        "latest_features":     None,

        # Aggregates.
        "total_monthly_value_myr": round(monthly_value, 2),
        "lifetime_quarantine_count": 0,

        # Bucketed transaction arrays (filled by transaction seeder).
        "txn_buckets":         [],

        # Idempotency marker.
        "_seed_marker":        _SEED_MARKER,
    }
    # AutoEmbed payload — Atlas indexes `embed_source.text` via the
    # commercial customers AutoEmbed index. Composed last so the builder
    # sees the full document.
    doc["embed_source"] = {"text": build_customer_embed_text(doc)}
    return doc


def _build_outlet(
    rng: random.Random, parent: dict, parent_idx: int, outlet_idx: int,
) -> dict:
    cid = f"comm_outlet_{parent_idx:02d}_{outlet_idx:02d}"
    account_id = _new_account_id("OUT", parent_idx, outlet_idx)
    outlet_id = f"OUT_{parent_idx:02d}_{outlet_idx:02d}"
    parent_bp = parent["business_profile"]
    state = parent["address"]["state"]
    industry = parent_bp["industry"]

    # Prefer the parent's curated outlet labels (from COMMERCIAL_BUSINESSES);
    # fall back to a KL/Klang-Valley venue name if the curated list is exhausted.
    realism_biz = COMMERCIAL_BUSINESSES[(parent_idx - 1) % len(COMMERCIAL_BUSINESSES)]
    curated_outlets = realism_biz[3]
    if outlet_idx <= len(curated_outlets):
        outlet_label = curated_outlets[outlet_idx - 1]
    else:
        outlet_label = f"{realism_biz[0].split(' Sdn Bhd')[0].split(' Bhd')[0]} @ {KL_VENUES[(outlet_idx - 1) % len(KL_VENUES)]}"
    name = f"{parent['name']} - {outlet_label}"
    biz_reg = parent_bp["business_registration_no"]

    picks = _pick_packages(rng, max_n=1)
    primary = picks[0]
    subscriptions = _build_subscriptions(rng, picks)
    monthly_value = sum(s["monthly_fee_myr"] for s in subscriptions)

    registered_address = _build_v3_address(rng, state)
    contact_address = _build_v3_address(rng, state)
    contact = _build_contact(
        rng, idx_seed=parent_idx * 100 + outlet_idx,
    )
    # Override with an outlet-specific email.
    contact["email"] = (
        f"outlet{outlet_idx:02d}@acmebiz-{parent_idx:02d}.example.my"
    )

    business_profile = {
        "business_name":          parent_bp["business_name"],
        "business_registration_no": biz_reg,
        "business_type":          parent_bp["business_type"],
        "industry":               industry,
        "venue_capacity":         rng.randint(20, 200),
        "outlet_label":           outlet_label,
        "registered_address":     registered_address,
        "contract_terms":         parent_bp["contract_terms"],
    }

    entity_profiles = _build_entity_profiles_business(
        rng,
        primary_pkg=primary,
        outlet_count=None,
        contract_terms=parent_bp["contract_terms"],
        parent_customer_id=parent["customer_id"],
    )
    entities = list(entity_profiles.keys())

    cem = _build_cross_entity_metrics(
        rng, monthly_value=monthly_value, outlet_dist=None,
    )
    spend_trend_label = _spend_trend_label(cem["monthly_spend_trend_12m"])
    recommendations = _build_recommendations(
        rng,
        churn=cem["churn_risk"],
        tier=parent["tier"],
        entities=entities,
        monthly_spend_trend=spend_trend_label,
    )
    outlet_tenure_months = rng.randint(6, 36)

    doc = {
        "_schema_version":     SCHEMA_VERSION_V3,
        "customer_id":         cid,
        "name":                name,
        "customer_type":       "commercial",
        "account_id":          account_id,
        "parent_account_id":   parent["account_id"],
        "outlet_id":           outlet_id,
        "tier":                parent["tier"],

        "preferred_name":  None,
        "ic_number":       None,
        "date_of_birth":   None,
        "ethnicity":       None,
        "gender":          None,
        "marital_status":  None,
        "household_size":  None,
        "occupation_band": None,

        "contact":             contact,
        "address":             contact_address,

        "business_profile":    business_profile,
        "entities":            entities,
        "entity_profiles":     entity_profiles,
        "cross_entity_metrics": cem,
        "brand_journey":       [],
        "interaction_history": {
            "support_interactions":    [],
            "marketing_interactions":  [],
            "channel_engagement_rates": {},
        },
        "active_campaigns":    [],
        "recommendations":     recommendations,

        "equipment":           _build_equipment(rng, count=rng.randint(1, 3)),
        "current_cycle":       _build_current_cycle(rng, expected=monthly_value),

        "subscriptions":       subscriptions,
        "active_promotions":   _build_promotions(
            rng, tier=parent["tier"], tenure_months=outlet_tenure_months,
        ),
        "entitlements":        _build_entitlements(rng),
        "recent_transactions": [],
        "open_cases":          [],
        "latest_features":     None,

        "total_monthly_value_myr": round(monthly_value, 2),
        "lifetime_quarantine_count": 0,

        "txn_buckets":         [],

        "_seed_marker":        _SEED_MARKER,
    }
    doc["embed_source"] = {"text": build_customer_embed_text(doc)}
    return doc


def _attach_outlet_revenue_to_parent(parent: dict, outlets: list[dict]) -> None:
    """Populate `cross_entity_metrics.outlet_revenue_distribution_30d` on the
    parent doc once outlets are built. Uses a synthesised 30-day revenue figure
    per outlet derived from its monthly subscription value."""
    dist: list[dict] = []
    for o in outlets:
        dist.append({
            "outlet_id":      o["outlet_id"],
            "outlet_label":   o["business_profile"]["outlet_label"],
            "revenue_30d_myr": round(o["total_monthly_value_myr"], 2),
        })
    parent["cross_entity_metrics"]["outlet_revenue_distribution_30d"] = dist


# --- Public entrypoint --------------------------------------------------

async def main(
    db,
    *,
    parent_count: int = 5,
    outlets_per_parent_min: int = 2,
    outlets_per_parent_max: int = 4,
) -> dict:
    """Seed `customers_commercial` + `customer_index` (commercial rows).

    Returns counters: parents_inserted, outlets_inserted,
    customer_index_inserted.
    """
    if outlets_per_parent_min > outlets_per_parent_max:
        raise ValueError(
            "outlets_per_parent_min must be <= outlets_per_parent_max"
        )

    rng = random.Random(37)

    coll_comm = db[CUSTOMERS_COMMERCIAL]
    coll_idx = db[CUSTOMER_INDEX]

    # Idempotent reset (delete-by-marker only — never wipe the residential
    # index rows, which carry no marker).
    await coll_comm.delete_many({"_seed_marker": _SEED_MARKER})
    await coll_idx.delete_many({"_seed_marker": _SEED_MARKER})

    parents_inserted = 0
    outlets_inserted = 0
    index_inserted = 0

    for i in range(1, parent_count + 1):
        n_outlets = rng.randint(outlets_per_parent_min, outlets_per_parent_max)
        parent = _build_parent(rng, i, n_outlets)

        outlets: list[dict] = [
            _build_outlet(rng, parent, i, j)
            for j in range(1, n_outlets + 1)
        ]
        _attach_outlet_revenue_to_parent(parent, outlets)

        await coll_comm.insert_one(parent)
        parents_inserted += 1
        await coll_idx.insert_one(_index_doc(parent))
        index_inserted += 1

        for o in outlets:
            await coll_comm.insert_one(o)
            outlets_inserted += 1
            await coll_idx.insert_one(_index_doc(o))
            index_inserted += 1

    counters = {
        "parents_inserted":         parents_inserted,
        "outlets_inserted":         outlets_inserted,
        "customer_index_inserted":  index_inserted,
    }
    logger.info("seed_customers_commercial_done", **counters)
    return counters
