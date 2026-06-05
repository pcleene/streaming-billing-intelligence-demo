"""Acme-themed realism content for seed data.

Centralised, deterministic Malaysia-flavoured strings used by every
seed script so cross-references stay coherent: a customer's PPV
history, the matching transaction items, the commercial outlet's
parent name, and the quarantine case all reference the same pool of
content_ids / package codes / promo codes / venue names.

Pure data + small composition helpers. No Faker, no random seeds, no
I/O. Callers pass in their own ``random.Random(seed=…)`` instance to
keep the seed determinism contract intact.
"""

from __future__ import annotations

import random
from typing import Final


# =====================================================================
# Subscription packages
# =====================================================================
# (package_code, package_name, monthly_price_myr, segment_hint, target_tier)
#
# Mirrors Acme Malaysia's public lineup at a high level (price points
# illustrative, not authoritative). The `target_tier` column keeps the
# composition consistent between seed_customers.py and the
# `customer_summary.tier` snapshot frozen on transactions.

PACKAGES: Final[list[tuple[str, str, float, str, str]]] = [
    # Value tier
    ("PKG_PREPAID_LITE",         "Acme PREPAID Lite",                  0.00,   "value",      "bronze"),
    ("PKG_PREPAID_PLUS",         "Acme PREPAID Plus",                  49.90,  "value",      "bronze"),
    ("PKG_VAANAVIL",          "Acme Vaanavil",                   65.00,  "value",      "silver"),
    ("PKG_XIAO_TAI_YANG",     "Acme Xiao Tai Yang",              70.00,  "value",      "silver"),
    ("PKG_GO_STREAMING",      "Acme GO Streaming",               29.90,  "value",      "bronze"),
    # Standard tier
    ("PKG_FAMILY_SD",         "Acme Family Pack SD",             89.95,  "standard",   "silver"),
    ("PKG_FAMILY_HD",         "Acme Family Pack HD",            139.95,  "standard",   "gold"),
    ("PKG_FAMILY_4K",         "Acme Family Pack 4K",            169.95,  "standard",   "gold"),
    ("PKG_MOVIES_PASS",       "Acme Movies Pass",                49.95,  "premium",    "gold"),
    ("PKG_SPORTS_PASS",       "Acme Sports Pass",                85.00,  "premium",    "gold"),
    # Premium tier
    ("PKG_SPORTS_PLUS",       "Acme Sports Plus",               120.00,  "premium",    "platinum"),
    ("PKG_MOVIES_PLUS",       "Acme Movies Plus",                85.00,  "premium",    "platinum"),
    ("PKG_MAHARAJA",          "Acme Maharaja",                  139.95,  "premium",    "gold"),
    ("PKG_ULTIMATE",          "Acme Ultimate Pack",             249.95,  "premium",    "platinum"),
    ("PKG_VARIETY_PLUS",      "Acme Variety Plus",              159.95,  "premium",    "platinum"),
    # Commercial tier
    ("PKG_BIZ_BASIC",         "Acme Business Basic",            349.00,  "commercial", "gold"),
    ("PKG_BIZ_SPORTS",        "Acme Business Sports",           699.00,  "commercial", "platinum"),
    ("PKG_BIZ_SPORTS_PLUS",   "Acme Business Sports Plus",     1000.00,  "commercial", "platinum"),
    ("PKG_BIZ_PREMIUM",       "Acme Business Premium",         1399.00,  "commercial", "platinum"),
    ("PKG_BIZ_HOSPITALITY",   "Acme Business Hospitality",     1799.00,  "commercial", "platinum"),
]


# =====================================================================
# Add-ons (OTT partnerships and a-la-carte channels)
# =====================================================================
# (addon_code, addon_name, partner, monthly_price_myr)

ADDONS: Final[list[tuple[str, str, str, float]]] = [
    ("ADDON_NETFLIX_BASIC",      "Netflix Basic add-on",            "Netflix",        19.00),
    ("ADDON_NETFLIX_STANDARD",   "Netflix Standard add-on",         "Netflix",        29.00),
    ("ADDON_NETFLIX_PREMIUM",    "Netflix Premium add-on",          "Netflix",        49.00),
    ("ADDON_DISNEY_HOTSTAR",     "Disney+ Hotstar add-on",          "Disney",         24.90),
    ("ADDON_HBO_GO",             "HBO Go add-on",                   "HBO",            34.90),
    ("ADDON_SOOKA_PREMIUM",      "sooka Premium",                   "sooka",           9.90),
    ("ADDON_IQIYI_VIP",          "iQIYI VIP add-on",                "iQIYI",          19.00),
    ("ADDON_VIU_PREMIUM",        "Viu Premium add-on",              "Viu",            14.90),
    ("ADDON_TVN_ASIA",           "tvN Asia channel add-on",         "tvN",            12.00),
    ("ADDON_BEIN_SPORTS",        "beIN Sports HD pack",             "beIN",           39.00),
    ("ADDON_CARTOON_NETWORK",    "Cartoon Network HD",              "WB Discovery",    9.95),
    ("ADDON_NICKELODEON_HD",     "Nickelodeon HD",                  "Paramount",       9.95),
    ("ADDON_DISCOVERY_PLUS",     "Discovery+ add-on",               "WB Discovery",   14.90),
]


# =====================================================================
# PPV titles (sports, concerts, movies, e-sports, cultural events)
# =====================================================================
# (content_id, title, price_myr, category)

PPV_TITLES: Final[list[tuple[str, str, float, str]]] = [
    # Live sports — boxing, MMA, F1, football
    ("PPV_PACQUIAO_LOPEZ",         "Pacquiao vs Lopez",                          25.00, "boxing"),
    ("PPV_FURY_USYK_3",            "Fury vs Usyk 3",                             39.90, "boxing"),
    ("PPV_UFC_KL_FIGHT_NIGHT",     "UFC Fight Night: Kuala Lumpur",              29.90, "mma"),
    ("PPV_F1_SINGAPORE_GP",        "F1 Singapore GP Live",                       29.90, "live_sports"),
    ("PPV_F1_SEPANG_GP",           "F1 Malaysia Sepang GP Live",                 34.90, "live_sports"),
    ("PPV_LIVERPOOL_CHELSEA",      "Liverpool vs Chelsea (live)",                35.00, "live_sports"),
    ("PPV_MANUTD_ARSENAL",         "Manchester United vs Arsenal (live)",        35.00, "live_sports"),
    ("PPV_EPL_DERBY_NIGHT",        "EPL Derby Night Triple-Header",              45.00, "live_sports"),
    ("PPV_SUPER_LEAGUE_FINAL",     "Super League Malaysia Final 2026",           14.90, "live_sports"),
    ("PPV_AFC_CUP_FINAL",          "AFC Cup Final 2026",                         24.90, "live_sports"),
    ("PPV_BADMINTON_THOMAS_CUP",   "Thomas Cup Final 2026",                      19.90, "live_sports"),
    ("PPV_SEA_GAMES_OPEN",         "SEA Games 2026 Opening Ceremony",             9.90, "cultural"),
    # Concerts
    ("PPV_MERDEKA_CONCERT",        "Merdeka Day Live Concert 2026",              19.90, "concert"),
    ("PPV_SITI_NURHALIZA_LIVE",    "Siti Nurhaliza Live in KL",                  29.90, "concert"),
    ("PPV_COLDPLAY_BUKIT_JALIL",   "Coldplay Live at Bukit Jalil",               79.90, "concert"),
    ("PPV_RAYA_VARIETY_NIGHT",     "Hari Raya Variety Night Live",               14.90, "concert"),
    ("PPV_MAYDAY_TAIPEI_LIVE",     "Mayday Live from Taipei",                    49.90, "concert"),
    # Movies / telemovies / specials
    ("PPV_RAYA_TELEMOVIE",         "Hari Raya Special Telemovie",                 9.90, "movie"),
    ("PPV_CNY_FAMILY_FILM",        "CNY Family Reunion Special",                  9.90, "movie"),
    ("PPV_DEEPAVALI_BLOCKBUSTER",  "Deepavali Tamil Blockbuster",                12.90, "movie"),
    ("PPV_OPPENHEIMER",            "Oppenheimer (Acme Premiere)",               14.90, "movie"),
    ("PPV_DUNE_PART_TWO",          "Dune: Part Two (Acme Premiere)",            14.90, "movie"),
    ("PPV_KL_GANGSTER_3",          "KL Gangster 3",                              12.90, "movie"),
    ("PPV_POLIS_EVO_3",            "Polis Evo 3",                                12.90, "movie"),
    # E-sports
    ("PPV_MLBB_M5_FINAL",          "MLBB M5 World Championship Final",           19.90, "esports"),
    ("PPV_DOTA2_TI_FINAL",         "Dota 2 TI Grand Final",                      24.90, "esports"),
    ("PPV_PMGC_GLOBAL_FINAL",      "PUBG Mobile Global Championship Final",      19.90, "esports"),
    ("PPV_VALORANT_CHAMPIONS",     "Valorant Champions Final",                   19.90, "esports"),
    # Cultural / variety
    ("PPV_NYE_COUNTDOWN_KLCC",     "New Year Countdown Live at KLCC",            12.90, "cultural"),
    ("PPV_MAULIDUR_RASUL_SPECIAL", "Maulidur Rasul Special Programme",            0.00, "cultural"),
    ("PPV_MOON_FESTIVAL_GALA",     "Mid-Autumn Moon Festival Gala",               9.90, "cultural"),
]


# =====================================================================
# Promotions (cultural anchors + loyalty / win-back)
# =====================================================================
# (promotion_code, description, default_discount_amount_myr,
#  (valid_from_iso, valid_to_iso), tier_eligibility)

PROMOTIONS: Final[list[tuple[str, str, float, tuple[str, str], tuple[str, ...]]]] = [
    ("PROMO_RAYA_2026",
     "Hari Raya Aidilfitri RM30 rebate (3 months)",
     30.00, ("2026-04-01", "2026-06-30"),
     ("silver", "gold", "platinum")),
    ("PROMO_CNY_2026",
     "Chinese New Year RM50 rebate (2 months)",
     50.00, ("2026-02-01", "2026-03-31"),
     ("gold", "platinum")),
    ("PROMO_DEEPAVALI_2026",
     "Deepavali RM25 rebate",
     25.00, ("2026-10-15", "2026-11-30"),
     ("silver", "gold", "platinum")),
    ("PROMO_MERDEKA_2026",
     "Merdeka Special: 1 month free Movies Pass",
     49.95, ("2026-08-15", "2026-09-30"),
     ("gold", "platinum")),
    ("PROMO_LOYALTY_5Y",
     "5-year loyalty discount: 10% off Family Pack",
     15.99, ("2025-01-01", "2026-12-31"),
     ("gold", "platinum")),
    ("PROMO_LOYALTY_10Y",
     "10-year loyalty: 15% off any pack + free Movies Pass for 3 months",
     30.00, ("2025-01-01", "2026-12-31"),
     ("platinum",)),
    ("PROMO_RETENTION_WINBACK",
     "Win-back: free Movies Pass for 2 months",
     49.95, ("2026-01-01", "2026-12-31"),
     ("bronze", "silver", "gold")),
    ("PROMO_NEW_CUSTOMER_3M",
     "New customer welcome: 50% off first 3 months",
     69.97, ("2026-01-01", "2026-12-31"),
     ("bronze", "silver")),
    ("PROMO_BUNDLE_OTT",
     "Bundle deal: Netflix + Disney+ Hotstar at 20% off",
     8.78, ("2026-01-01", "2026-12-31"),
     ("gold", "platinum")),
    ("PROMO_YEAR_END_2025",
     "Year-end RM40 rebate (December only)",
     40.00, ("2025-12-01", "2025-12-31"),
     ("silver", "gold", "platinum")),
    ("PROMO_BIRTHDAY_GIFT",
     "Birthday month: free PPV title (up to RM 25)",
     25.00, ("2026-01-01", "2026-12-31"),
     ("gold", "platinum")),
    ("PROMO_REFERRAL_RM30",
     "Referral bonus: RM30 off when a friend signs up",
     30.00, ("2026-01-01", "2026-12-31"),
     ("bronze", "silver", "gold", "platinum")),
]


# =====================================================================
# Charge codes (mirrors the catalog the app already seeds)
# =====================================================================

CHARGE_CODES: Final[tuple[str, ...]] = (
    "CC_SUB_MTHLY",       # standard monthly subscription
    "CC_PPV_STD",         # standard PPV
    "CC_PPV_DIRECT",      # direct PPV (no rights chain)
    "CC_BUNDLE_PPV",      # PPV inside a bundle promo
    "CC_ADDON_STD",       # standard add-on
    "CC_DEVICE_RENT",     # decoder rental fee
    "CC_DEVICE_PUR",      # decoder purchase
    "CC_LATE",            # late-payment fee
    "CC_ETF",             # early termination fee
    "CC_PROMO_REBATE",    # promo rebate (negative line)
    "CC_REFUND",          # full / partial refund
    "CC_ADJ",             # manual adjustment
    "CC_PRORATION",       # prorated charge for mid-cycle changes
    "CC_INSTALL_FEE",     # installation / activation
    "CC_DEPOSIT",         # security deposit (commercial)
)


# =====================================================================
# Commercial businesses (parent customer accounts)
# =====================================================================
# (legal_name, business_type, ssm_number, outlet_labels)

COMMERCIAL_BUSINESSES: Final[list[tuple[str, str, str, list[str]]]] = [
    ("Pelita Holdings Sdn Bhd", "restaurant", "201501023456",
     ["Pelita KLCC", "Pelita Ampang", "Pelita Bukit Bintang", "Pelita Subang", "Pelita JB CIQ"]),
    ("Restoran Nasi Kandar Original Sdn Bhd", "restaurant", "200912034455",
     ["Original SS2", "Original Penang Road", "Original Mid Valley"]),
    ("Sunway Hospitality Group Bhd", "hotel", "199801087712",
     ["Sunway Resort Hotel", "Sunway Velocity Hotel", "Sunway Pyramid Hotel"]),
    ("FitnessFirst Malaysia Sdn Bhd", "gym", "200501112266",
     ["FitnessFirst Pavilion", "FitnessFirst Mid Valley", "FitnessFirst KLCC"]),
    ("Old Town Kopitiam Co. Sdn Bhd", "cafe", "201801009912",
     ["Old Town Bangsar", "Old Town SS15", "Old Town Damansara Utama"]),
    ("Kenanga Mamak Sdn Bhd", "restaurant", "201601044123",
     ["Kenanga TTDI", "Kenanga Damansara", "Kenanga Setapak"]),
    ("Berjaya Mart Retail Sdn Bhd", "retail", "199401088901",
     ["Berjaya Mart Times Square", "Berjaya Mart Sungai Wang", "Berjaya Mart Plaza Low Yat"]),
    ("Genting Highlands Hospitality Bhd", "hotel", "198801019945",
     ["Resorts World Genting Hotel", "First World Hotel", "Theme Park Hotel"]),
    ("KL Sports Bar Holdings Sdn Bhd", "bar", "201501099812",
     ["Score Bar TRX", "Score Bar Bangsar", "Score Bar Plaza Damas"]),
    ("Mahkota Tamil Restaurant Sdn Bhd", "restaurant", "201001028733",
     ["Mahkota Brickfields", "Mahkota PJ Old Town", "Mahkota Klang"]),
    ("Pavilion Cinema Group Sdn Bhd", "cinema", "200201066421",
     ["Pavilion KL Cineplex", "Pavilion Bukit Jalil Cineplex"]),
    ("Sabah Hotel Network Sdn Bhd", "hotel", "200001012347",
     ["Hyatt Regency KK", "Le Meridien KK", "Sutera Harbour Resort"]),
]


# =====================================================================
# KL / Klang Valley venue names for outlet labels
# =====================================================================

KL_VENUES: Final[tuple[str, ...]] = (
    "Suria KLCC", "Pavilion KL", "Mid Valley Megamall", "1 Utama",
    "Sunway Pyramid", "The Gardens Mall", "Berjaya Times Square",
    "Bangsar Shopping Centre", "Lot 10", "TRX Exchange",
    "IOI City Mall", "Sunway Velocity", "Setia City Mall",
    "Pavilion Bukit Jalil", "MyTOWN Cheras", "Quill City Mall",
    "Plaza Mont Kiara", "Avenue K", "NU Sentral",
    "RetailGroup Bukit Tinggi", "RetailGroup Mid Valley", "Empire Damansara",
    "Citta Mall", "Atria Damansara Jaya", "The Curve",
    "eCurve", "Tropicana Gardens Mall", "KL East Mall",
    "Pearl Shopping Gallery", "Pertama Complex",
)


# =====================================================================
# Support narratives — keyed by (category, subcategory)
# =====================================================================
# Each entry is a 2-3 sentence narrative template. Format placeholders
# the caller fills: {tier}, {state}, {amount}, {promo_name}, {promo_code},
# {valid_to}, {month}, {ticket_num}, {points}, {redeemed}, {outlet1},
# {outlet2}, {quote_total}, {decoder_serial}, {firmware}, {channel_pack}.

SUPPORT_NARRATIVES: Final[dict[tuple[str, str], list[str]]] = {
    ("billing", "discount_query"): [
        "Customer queried RM{amount} {promo_name} rebate that did not appear on the {month} bill. Confirmed promo {promo_code} is active in CRM through {valid_to}; explained the next billing cycle will auto-apply. No refund requested.",
        "Caller noticed {promo_name} discount missing on auto-debit. Investigated and found a CRM-to-billing sync delay — raised JIRA-{ticket_num} for the data team. Customer accepted credit on next cycle.",
        "{tier}-tier customer asked why {promo_code} disappeared from the latest invoice. Promo expired on {valid_to} and the system recognised the cycle correctly; confirmed eligible loyalty options as a follow-up.",
    ],
    ("billing", "amount_dispute"): [
        "Customer disputed the RM{amount} charge on this month's bill. Walked through line items: subscription, mid-cycle add-on (Disney+ Hotstar), and prorated balance. Customer accepted the explanation and waived the dispute.",
        "{tier}-tier subscriber escalated a perceived overcharge. Reviewed transaction extref against the cycle and confirmed both line items are valid (subscription + ETF for early downgrade). Issued a goodwill RM10 credit to retain.",
    ],
    ("billing", "refund_request"): [
        "Customer requested a partial refund for the {month} cycle after a 36-hour signal outage in {state}. Approved RM{amount} credit on the next bill in line with the SLA matrix; logged JIRA-{ticket_num} for capacity tracking.",
        "Caller asked for a full refund after cancelling within the 14-day window. Verified the cancellation date, processed RM{amount} refund to the original payment method, and disabled auto-renewal on the account.",
    ],
    ("technical", "decoder_signal_loss"): [
        "Signal loss after a thunderstorm in {state}. Walked customer through a soft-reset of the Acme Ultra Box; channels restored without a truck-roll. Logged event for capacity planning.",
        "Decoder STB-{decoder_serial} lost lock; firmware was {firmware} (current ULTRA-9.4.2). Pushed a remote firmware update; customer confirmed signal restored after reboot.",
        "Recurring HD signal pixelation during heavy rain in {state}. Booked a free realignment visit (work-order WO-{ticket_num}); customer was satisfied with the response time.",
    ],
    ("technical", "decoder_replacement"): [
        "Customer reported decoder STB-{decoder_serial} repeatedly rebooting. Diagnosed a faulty power supply; dispatched a replacement Acme Ultra Box and scheduled the engineer for the next day. Open WO-{ticket_num}.",
        "Old PREPAID decoder failed to read smart card. Confirmed device EOL and recommended an Acme Ultra Box upgrade with the loyalty pricing for {tier} tier; customer confirmed via WhatsApp same day.",
    ],
    ("technical", "streaming_app"): [
        "Acme GO app would not authenticate on the customer's iPhone. Walked through clearing the cache, reinstalling, and re-pairing with the smart card. Login restored; logged FAQ-{ticket_num} for the mobile team.",
        "Streaming buffered repeatedly on Acme GO at peak hours. Confirmed CDN routing for the customer's ISP, suggested a 5GHz Wi-Fi switch; follow-up in 7 days. Customer satisfied with the quick triage.",
    ],
    ("loyalty", "points_query"): [
        "Customer queried the {points} ALR points balance and asked about redemption. Walked through the streaming-addon redemption flow; customer redeemed {redeemed} points for a Movies Pass extension.",
        "Confirmed point accrual rate for the {tier} tier (1 point per RM 1, plus a 1.5x multiplier on PPV). Customer was surprised the multiplier did not apply to add-on charges; logged FAQ-{ticket_num} for the content team.",
    ],
    ("loyalty", "tier_upgrade"): [
        "Customer hit the {tier}-tier threshold this cycle. Confirmed the new perks: priority hotline, free Movies Pass for 1 month, and a 5% multiplier on PPV. Customer accepted the auto-enrolment.",
        "Reviewed the customer's spend trajectory; on track to upgrade in 2 months. Suggested keeping the Sports Pass active to lock in the milestone. Customer thanked us and asked to be reminded mid-cycle.",
    ],
    ("renewal", "contract_negotiation"): [
        "Renewal discussion with the account manager. Pelita Holdings is considering adding 2 outlets ({outlet1}, {outlet2}). Quoted RM{quote_total} with a 5% early-bird incentive valid through end of cycle.",
        "Customer wants to extend the EPL 2025/26 entitlement for one more season. Proposed a 12-month renewal with a free upgrade to Acme Business Sports Plus 4K; awaiting their procurement sign-off.",
        "Caller is comparing offers from competitor IPTV operators. Reviewed the {channel_pack} value-add and offered a 12-month lock-in at RM{quote_total}; sent the e-quote and a follow-up reminder for next week.",
    ],
    ("renewal", "early_termination"): [
        "Customer requested early termination citing a relocation overseas. Calculated the ETF (RM{amount}) and offered a 50% discount under the relocation policy; customer agreed and signed the form.",
        "Caller wanted to terminate the lock-in early after a service complaint. Reviewed the previous JIRA-{ticket_num} resolution, offered a 1-month service credit, and the customer agreed to stay through the cycle.",
    ],
    ("retention", "save_call"): [
        "Customer called to cancel after seeing a competitor's promo. Offered the {promo_name} (RM{amount} off, {valid_to}) plus 2 free PPV credits; customer agreed to a 6-month extension.",
        "Win-back attempt: customer churned 3 months ago. Offered the New Customer Welcome 50% off for 3 months and waived the reactivation fee. Customer accepted and reactivated the {channel_pack}.",
    ],
    ("commercial", "outlet_onboarding"): [
        "New outlet ({outlet1}) joining the Pelita group account. Walked the GM through the Acme Business hospitality SLA and the per-outlet billing structure. Activation scheduled for the first business day of next month.",
        "Group account (FitnessFirst) onboarding 2 new branches. Confirmed the parent-account billing arrangement and the consolidated invoice format. Quote RM{quote_total} per month total; awaiting procurement approval.",
    ],
}


# =====================================================================
# Per-pattern learnings narratives for quarantine_cases_history
# =====================================================================
# Keys MUST match the LEARNING_PATTERNS list in seed_history.py.

LEARNING_NARRATIVES: Final[dict[str, dict[str, str]]] = {
    "crm_promo_sync_lag": {
        "pattern_name": "crm_lag_during_promo_rollout",
        "root_cause": (
            "CRM-to-billing batch sync exceeded {lag_hours} hours during the "
            "{promo_name} rollout. Promotions ingested into CRM after the "
            "02:00 KL nightly billing run were not visible to the rule engine "
            "until the next cycle, so the rebate appeared missing on this bill."
        ),
        "what_would_have_prevented": (
            "A real-time CRM-to-billing data path (Mongo change stream into the "
            "Atlas Stream Processing pipeline) would have surfaced the new "
            "promotion within seconds and prevented the false-positive quarantine."
        ),
    },
    "merchant_retry_idempotency_collision": {
        "pattern_name": "merchant_retry_dup_idempotency",
        "root_cause": (
            "The merchant retried a webhook after a 504 response from our "
            "gateway. Both attempts carried the same idempotency key but the "
            "gateway treated them as distinct events because the retry header "
            "was stripped by an upstream proxy, producing a duplicate-charge "
            "false positive on the customer's account."
        ),
        "what_would_have_prevented": (
            "End-to-end propagation of the X-Idempotency-Key header plus a "
            "24-hour dedupe window keyed on (customer_id, charge_code, amount, "
            "ts_minute). The dedupe is already implemented for direct charges; "
            "extend it to merchant webhook ingest."
        ),
    },
    "household_bulk_ppv_burst": {
        "pattern_name": "household_legitimate_burst",
        "root_cause": (
            "A single household legitimately purchased {burst_count} PPV titles "
            "within {burst_minutes} minutes during a sports weekend (F1 + EPL "
            "+ boxing card). The velocity rule fires on any 5+ PPV in 5 minutes "
            "which is too aggressive for live-event clusters where every household "
            "member buys in parallel from different decoders on the same smart card."
        ),
        "what_would_have_prevented": (
            "Tighten the velocity rule to consider distinct content_categories "
            "and active-decoder count: multiple live_sports titles within the same "
            "evening are a recognised legitimate Acme pattern, especially during "
            "marquee weekends."
        ),
    },
    "out_of_region_legitimate_travel": {
        "pattern_name": "travel_mode_geo_anomaly",
        "root_cause": (
            "Customer travelled from {home_state} to {away_state}; the PPV "
            "purchase originated from a hotel Wi-Fi in {away_state}. The "
            "geographic-anomaly rule does not consult the customer's travel-mode "
            "flag (set when they enable Acme GO streaming on a non-home device)."
        ),
        "what_would_have_prevented": (
            "Honour the travel_mode flag end-to-end: when set within the last 7 "
            "days, the geo rule should suppress for that customer until expiry. "
            "Add an ASP filter stage that joins the live customer doc."
        ),
    },
    "device_swap_velocity_signature": {
        "pattern_name": "device_swap_velocity",
        "root_cause": (
            "Two decoder swaps within {swap_window_hours} hours from the same "
            "household triggered the velocity rule. The swaps were legitimate — "
            "a faulty Acme Ultra Box was returned and replaced under warranty — "
            "but the rule does not correlate with the field-engineer work-order ticket."
        ),
        "what_would_have_prevented": (
            "Cross-reference the field-engineer work order: any decoder swap "
            "with an open work-order ticket should suppress the velocity rule "
            "for 72h. Work-order metadata is already in CRM; expose it through "
            "the Customer 360 endpoint the rule engine reads."
        ),
    },
    "mid_cycle_addon_amount_outlier": {
        "pattern_name": "addon_proration_misclassified",
        "root_cause": (
            "Customer added Disney+ Hotstar mid-cycle; the prorated charge "
            "appeared in the same statement window as the standard subscription, "
            "creating an amount that exceeded the 30-day rolling average by "
            "{outlier_ratio}x. The amount_outlier rule fires before the "
            "proration_check rule has a chance to label the line."
        ),
        "what_would_have_prevented": (
            "The proration_check rule should pre-empt the amount_outlier rule "
            "when the customer has an addon_added event in the cycle. Add a "
            "rule-precedence override in the ASP pipeline ordering."
        ),
    },
    "termination_fee_underbilled": {
        "pattern_name": "etf_rate_table_drift",
        "root_cause": (
            "Early termination billed RM {actual_etf} — half of the contractually "
            "expected (6 remaining months × RM 99.95 = RM {expected_etf}). "
            "Configuration error in the lock-in rate table after the 2025-Q4 "
            "pricing update; the new rate did not propagate to the legacy "
            "contract bucket."
        ),
        "what_would_have_prevented": (
            "An automated reconciliation job comparing the quarter's lock-in "
            "rate table against the active contracts; alert when any contract's "
            "stored rate differs from the catalog by >5%. Add to the monthly "
            "drift dashboard."
        ),
    },
}


# =====================================================================
# Disposition × pattern → analyst-note narratives
# =====================================================================
# Each entry is a 2–4 sentence narrative. Placeholders the caller may fill:
# {tier}, {state}, {pattern_name}, {amount}, {promo_name}, {promo_code}.

ANALYST_NOTE_TEMPLATES: Final[dict[tuple[str, str], list[str]]] = {
    ("legitimate", "crm_promo_sync_lag"): [
        "Confirmed the {promo_name} rebate is active in CRM; the warehouse view "
        "we ran the rule against was {lag_hours}h stale. The next billing run "
        "will pick up the rebate automatically — no customer action required.",
        "Reviewed the CRM record for {promo_code}: promo is valid through "
        "{valid_to}, eligible at {tier} tier. The rule fired on the lag-snapshot; "
        "case is a documented false positive.",
    ],
    ("legitimate", "household_bulk_ppv_burst"): [
        "Customer is a {tier}-tier household in {state} with a recognised live-sports "
        "viewing pattern. The {burst_count} PPV purchases mapped to F1, EPL, and a "
        "boxing card on the same Saturday — all distinct content_categories, all "
        "from the same registered smart card. Confirmed legitimate.",
        "Velocity rule is too tight for marquee weekends. The household has 4 "
        "active decoders and a sports-pass history; suggest tightening the rule "
        "to consider distinct categories rather than raw burst count.",
    ],
    ("legitimate", "out_of_region_legitimate_travel"): [
        "Customer enabled travel_mode 4 days before the {away_state} purchase "
        "via the Acme GO app. The IP geolocation matched a hotel Wi-Fi pool "
        "we have on file. The geo rule did not consult the travel flag — "
        "filing the rule-improvement ticket against the ASP pipeline.",
    ],
    ("legitimate", "device_swap_velocity_signature"): [
        "Decoder swap was scheduled by the field-engineer team (work order "
        "WO-{ticket_num}) after a verified hardware fault. The velocity rule "
        "lacks the work-order linkage; recommended adding a 72h suppression "
        "window when a decoder-swap WO is open.",
    ],
    ("legitimate", "mid_cycle_addon_amount_outlier"): [
        "Customer added {addon_name} mid-cycle on {addon_date}. The proration "
        "drove the bill above the 30-day rolling average by {outlier_ratio}x. "
        "Both the addon_added event and the proration line are present in the "
        "transaction; rule-precedence fix recommended.",
    ],
    ("data_error", "termination_fee_underbilled"): [
        "ETF rate-table drift: the contract bucket carries the 2024-Q4 rate "
        "(RM 49.95/month) but the customer's plan was repriced in 2025-Q4 to "
        "RM 99.95/month. The legacy bucket was not migrated; raised JIRA-{ticket_num} "
        "for the billing-config team and credited the customer the delta.",
    ],
    ("data_error", "merchant_retry_idempotency_collision"): [
        "Two transaction inserts share the same idempotency key but were ingested "
        "as distinct events. Confirmed the upstream proxy strips the X-Idempotency-Key "
        "header; the gateway team is patching the proxy. One charge has been "
        "reversed and the customer was notified.",
    ],
    ("fraud", "household_bulk_ppv_burst"): [
        "Burst pattern is inconsistent with the customer's 30-day baseline "
        "(usually 0–1 PPV/week). Smart-card was last seen registered in "
        "{home_state} but the purchase originated from {away_state} with no "
        "travel_mode flag. Forwarded to the fraud team and suspended PPV until review.",
    ],
    ("fraud", "out_of_region_legitimate_travel"): [
        "Without an active travel_mode and with no Acme GO sign-in events, "
        "the {away_state} purchase looks like card cloning. Smart-card frozen "
        "and customer notified via SMS to call us back; awaiting verification.",
    ],
}


# =====================================================================
# Helper functions
# =====================================================================

def pick_promotion_for(
    rng: random.Random,
    *,
    tier: str | None,
    tenure_months: int = 0,
) -> tuple[str, str, float, tuple[str, str]]:
    """Return ``(promotion_code, description, amount_myr, (valid_from, valid_to))``.

    Filters by tier eligibility; falls back to a tier-agnostic promo if the
    requested tier has no exact match.
    """
    eligible = [p for p in PROMOTIONS if not p[4] or (tier and tier in p[4])]
    if not eligible:
        eligible = list(PROMOTIONS)
    # Loyalty promos for long-tenured customers, win-backs for new ones, etc.
    if tenure_months >= 60:
        loyalty = [p for p in eligible if "LOYALTY" in p[0]]
        if loyalty:
            chosen = rng.choice(loyalty)
            return (chosen[0], chosen[1], chosen[2], chosen[3])
    if tenure_months <= 3:
        welcome = [p for p in eligible if "NEW_CUSTOMER" in p[0] or "WINBACK" in p[0]]
        if welcome:
            chosen = rng.choice(welcome)
            return (chosen[0], chosen[1], chosen[2], chosen[3])
    chosen = rng.choice(eligible)
    return (chosen[0], chosen[1], chosen[2], chosen[3])


def pick_ppv_title(
    rng: random.Random,
    *,
    category: str | None = None,
) -> tuple[str, str, float, str]:
    """Return ``(content_id, title, price_myr, category)``. Optional filter by category."""
    pool = [t for t in PPV_TITLES if category is None or t[3] == category]
    if not pool:
        pool = list(PPV_TITLES)
    return rng.choice(pool)


def pick_addon(rng: random.Random) -> tuple[str, str, str, float]:
    return rng.choice(ADDONS)


def pick_package(
    rng: random.Random,
    *,
    target_tier: str | None = None,
    segment: str | None = None,
) -> tuple[str, str, float, str, str]:
    """Pick a package biased to the customer tier / segment."""
    pool = list(PACKAGES)
    if target_tier:
        bias = [p for p in pool if p[4] == target_tier]
        if bias:
            pool = bias
    if segment:
        bias = [p for p in pool if p[3] == segment]
        if bias:
            pool = bias
    return rng.choice(pool)


def pick_support_category(
    rng: random.Random,
    *,
    tier: str | None = None,
    churn_band: str | None = None,
) -> tuple[str, str]:
    """Pick a (category, subcategory) pair biased by tier and churn band."""
    if churn_band == "high":
        weighted = [
            ("retention", "save_call"),
            ("renewal", "early_termination"),
            ("billing", "amount_dispute"),
            ("billing", "refund_request"),
        ]
    elif tier in ("platinum", "gold"):
        weighted = [
            ("loyalty", "points_query"),
            ("loyalty", "tier_upgrade"),
            ("billing", "discount_query"),
            ("technical", "decoder_signal_loss"),
            ("technical", "streaming_app"),
            ("renewal", "contract_negotiation"),
        ]
    else:
        weighted = [
            ("billing", "discount_query"),
            ("billing", "amount_dispute"),
            ("technical", "decoder_signal_loss"),
            ("technical", "streaming_app"),
            ("technical", "decoder_replacement"),
        ]
    return rng.choice(weighted)


def render_support_narrative(
    rng: random.Random,
    *,
    category: str,
    subcategory: str,
    context: dict[str, object],
) -> tuple[str, str]:
    """Return ``(short_summary, full_notes)`` from the SUPPORT_NARRATIVES pool."""
    templates = SUPPORT_NARRATIVES.get((category, subcategory))
    if not templates:
        # Defensive fallback — keep the seed running even if the caller
        # picks a (category, subcategory) we have not curated yet.
        full = (
            f"Customer raised a {category}/{subcategory} query. Acknowledged "
            "and routed to the appropriate team; logged for follow-up."
        )
        return full[:120].rstrip(), full
    template = rng.choice(templates)
    try:
        full = template.format(**context)
    except (KeyError, IndexError):
        full = template
    short = full.split(". ")[0].strip().rstrip(".")
    if len(short) > 160:
        short = short[:160].rstrip()
    return short, full


def render_offer(
    rng: random.Random,
    *,
    offer_type: str,
    tier: str | None,
    drivers: list[str] | None = None,
    monthly_spend_trend: str | None = None,
    entities: list[str] | None = None,
) -> tuple[str, str]:
    """Return ``(title, rationale)`` for a recommended offer."""
    drivers = drivers or []
    entities = entities or []
    catalogue = {
        "upgrade": (
            (
                "Upgrade to Acme Family Pack 4K",
                f"Spend trend {monthly_spend_trend or 'rising'} and engagement above the "
                f"{tier or 'silver'}-tier median; the 4K pack closes the value gap at +RM30/month.",
            ),
            (
                "Upgrade to Acme Sports Plus",
                "Customer's PPV history concentrates on live_sports (EPL + F1 weekends). "
                "Sports Plus replaces 4 separate PPV purchases for less than the rolling spend.",
            ),
        ),
        "addon": (
            (
                "Add Disney+ Hotstar to your plan",
                "Strong signal on family-content viewing windows; bundle pricing is "
                "RM24.90/month vs RM34.90 standalone, a clear retention play.",
            ),
            (
                "Add Netflix Standard via Acme",
                "Customer redeemed Acme GO ALR points twice this quarter; bundling "
                "Netflix on a single bill simplifies the renewal conversation.",
            ),
        ),
        "retention_discount": (
            (
                "Stay With Us: 20% off Acme Premium for 6 Months",
                "Churn-risk drivers: " + (", ".join(drivers) if drivers else "spend trend, support frequency") +
                ". A targeted 6-month discount keeps the lock-in alive past the next renewal review.",
            ),
            (
                "Loyalty Lock-In: RM 30/month off for 12 months",
                f"{tier or 'gold'}-tier customer with {len(entities)} entity (entities) on file. "
                "12-month price-lock with no commitment uplift is the best fit for the spend trajectory.",
            ),
        ),
        "winback": (
            (
                "Win Back: free Movies Pass for 2 months",
                "Reactivation incentive — covers the Movies Pass (RM 49.95/month) for two "
                "months at no charge if the customer reactivates within 30 days.",
            ),
        ),
        "loyalty_perk": (
            (
                "Loyalty Perk: free PPV title every month",
                f"{tier or 'gold'}-tier with strong engagement on the Acme app inbox. "
                "Monthly free PPV (up to RM 25) reinforces the tier's premium feel.",
            ),
            (
                "Loyalty Perk: priority hotline + free decoder upgrade",
                "Churn band low and tenure ≥ 36 months. Priority hotline plus a free "
                "Acme Ultra Box hardware refresh anchors the next 12-month cycle.",
            ),
        ),
    }
    options = catalogue.get(offer_type) or catalogue["upgrade"]
    return rng.choice(options)


def render_analyst_note(
    rng: random.Random,
    *,
    disposition: str,
    pattern: str,
    context: dict[str, object],
) -> str:
    """Return a multi-sentence analyst note for ``(disposition, pattern)``.

    Falls back to a generic note if the pair is uncurated — the caller's
    seed remains deterministic.
    """
    templates = ANALYST_NOTE_TEMPLATES.get((disposition, pattern))
    if not templates:
        # Generic fallback by disposition.
        if disposition == "legitimate":
            template = (
                "Reviewed transaction context against the customer's profile. "
                "Pattern {pattern_name}; rule fired on stale-snapshot data and "
                "the live customer doc shows no fraud signal. Disposition: legitimate."
            )
        elif disposition == "data_error":
            template = (
                "Pattern {pattern_name}: configuration drift between the catalog and "
                "the live charging engine. Issued the corrective adjustment and raised "
                "an internal ticket against the data-platform team."
            )
        else:
            template = (
                "Pattern {pattern_name}: signals do not match the customer's baseline. "
                "Smart card frozen pending verification; customer notified via SMS."
            )
        try:
            return template.format(**context)
        except (KeyError, IndexError):
            return template
    template = rng.choice(templates)
    try:
        return template.format(**context)
    except (KeyError, IndexError):
        return template


def render_resolution_summary(
    *,
    disposition: str,
    actions_taken: list[str] | None,
    pattern: str,
    compensation_myr: float = 0.0,
) -> str:
    """Compose a 1–2 sentence resolution summary from disposition + actions + pattern."""
    actions_taken = actions_taken or []
    pretty_actions = ", ".join(a.replace("_", " ") for a in actions_taken) or "no further action"
    base = {
        "legitimate": (
            f"Confirmed legitimate ({pattern}); cleared the case after running "
            f"{pretty_actions}. No customer compensation required."
        ),
        "data_error": (
            f"Resolved as data error ({pattern}); applied {pretty_actions} and "
            f"credited the customer RM {compensation_myr:.2f}. Internal ticket "
            "raised against the upstream data team for permanent fix."
        ),
        "fraud": (
            f"Confirmed fraud ({pattern}); executed {pretty_actions}. Smart "
            "card frozen pending verification and forwarded to the fraud-ops "
            "queue for monitoring."
        ),
    }
    return base.get(
        disposition,
        f"{disposition.replace('_', ' ').title()} ({pattern}); applied {pretty_actions}.",
    )
