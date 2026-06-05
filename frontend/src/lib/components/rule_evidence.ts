// Human-readable summaries for rule evidence payloads.
//
// The pipelines under `backend/app/pipelines/rule_pipeline_builders.py`
// project a fixed shape per rule_type. This helper switches on that
// shape and renders fields the analyst actually needs — not raw JSON.
//
// IMPORTANT: keep the field names here aligned with the `$project`
// stage of each builder. If a pipeline changes its output, update the
// matching case below.

export interface FormattedEvidence {
  // One-line headline summary suitable for inline display next to the
  // rule name. Empty string when no meaningful field was found.
  summary: string;
  // Key/value pairs for the secondary chip row (already formatted
  // strings; never raw objects). Capped to ~6 entries.
  chips: Array<{ key: string; value: string }>;
}

function fmtMyrCompact(n: unknown): string {
  if (n == null || typeof n !== "number" || Number.isNaN(n)) return "—";
  return `MYR ${n.toFixed(2)}`;
}

function fmtNum(n: unknown, digits = 2): string {
  if (n == null) return "—";
  if (typeof n === "number") {
    return Number.isInteger(n) ? n.toString() : n.toFixed(digits);
  }
  return String(n);
}

function fmtPct(num: unknown, denom: unknown): string {
  if (
    typeof num !== "number" ||
    typeof denom !== "number" ||
    denom === 0 ||
    Number.isNaN(num) ||
    Number.isNaN(denom)
  ) {
    return "";
  }
  return `${((num / denom) * 100).toFixed(0)}%`;
}

function fmtList(v: unknown, max = 3): string {
  if (!Array.isArray(v)) return "";
  const items = v.filter((x) => x != null).map(String);
  if (items.length === 0) return "";
  if (items.length <= max) return items.join(", ");
  return `${items.slice(0, max).join(", ")} +${items.length - max}`;
}

// Keys we never expose as raw chips (they're noisy / already covered).
const _NOISY_KEYS = new Set<string>([
  "transaction_id",
  "customer_id",
  "rule_type",
  "rule_name",
  "merchant_id",
  "cycle_id",
  "timestamp",
  "_id",
]);

function buildChips(
  e: Record<string, unknown>,
  picks: string[]
): Array<{ key: string; value: string }> {
  // Prefer the curated picks; fall back to any non-noisy keys when the
  // curated set produced nothing.
  const out: Array<{ key: string; value: string }> = [];
  for (const k of picks) {
    const v = e[k];
    if (v == null || (Array.isArray(v) && v.length === 0)) continue;
    if (typeof v === "object" && !Array.isArray(v)) continue;
    out.push({ key: k, value: Array.isArray(v) ? fmtList(v, 4) : String(v) });
  }
  if (out.length > 0) return out;
  for (const [k, v] of Object.entries(e)) {
    if (_NOISY_KEYS.has(k) || k.startsWith("_")) continue;
    if (v == null || typeof v === "object") continue;
    out.push({ key: k, value: String(v) });
    if (out.length >= 4) break;
  }
  return out;
}

export function formatRuleEvidence(
  rule_type: string | undefined,
  evidence: Record<string, unknown> | undefined | null
): FormattedEvidence {
  if (!evidence || typeof evidence !== "object") {
    return { summary: "", chips: [] };
  }
  const e = evidence as Record<string, unknown>;
  let summary = "";
  let chipKeys: string[] = [];

  switch (rule_type) {
    case "discount_mismatch": {
      // Pipeline emits: total_myr, total_discount_myr.
      const total = e.total_myr;
      const disc = e.total_discount_myr;
      const pct = fmtPct(disc, total);
      summary =
        `discount ${fmtMyrCompact(disc)} on ${fmtMyrCompact(total)}` +
        (pct ? ` (${pct})` : "") +
        ` — no active promotion`;
      chipKeys = ["total_discount_myr", "total_myr"];
      break;
    }

    case "entitlement_mismatch": {
      // Pipeline emits: purchased_refs (list), entitled (list), total_myr.
      const purchased = Array.isArray(e.purchased_refs) ? (e.purchased_refs as unknown[]) : [];
      const entitled = Array.isArray(e.entitled) ? (e.entitled as unknown[]) : [];
      const missing = purchased.filter((p) => !entitled.includes(p));
      if (missing.length > 0) {
        summary = `purchased content not entitled: ${fmtList(missing, 2)}`;
      } else {
        summary = "PPV content has no matching entitlement";
      }
      chipKeys = ["purchased_refs", "entitled", "total_myr"];
      break;
    }

    case "amount_outlier": {
      // Pipeline emits: total_myr, _avg, _stddev.
      const v = e.total_myr;
      const avg = e._avg ?? e.avg;
      const sd = e._stddev ?? e.stddev;
      const sigma =
        typeof v === "number" && typeof avg === "number" && typeof sd === "number" && sd > 0
          ? Math.abs((v - avg) / sd).toFixed(1)
          : null;
      summary = sigma
        ? `${fmtMyrCompact(v)} is ${sigma}σ from avg ${fmtMyrCompact(avg)}`
        : `${fmtMyrCompact(v)} vs avg ${fmtMyrCompact(avg)}`;
      chipKeys = ["total_myr", "_avg", "_stddev"];
      break;
    }

    case "velocity_anomaly": {
      // Pipeline emits: txn_count, total_myr, first_at, last_at, txn_ids.
      const c = e.txn_count ?? e.txn_count_5m ?? e.count;
      const total = e.total_myr;
      summary =
        `${fmtNum(c, 0)} txns in window` +
        (typeof total === "number" ? ` totalling ${fmtMyrCompact(total)}` : "");
      chipKeys = ["txn_count", "total_myr", "first_at", "last_at"];
      break;
    }

    case "geographic_anomaly": {
      const txn_state = e.txn_state ?? e.state;
      const home = e.home_state ?? e.customer_state;
      const km = e.geographic_distance_from_home_km;
      summary =
        `transaction in ${String(txn_state ?? "?")}, ` +
        `customer home ${String(home ?? "?")}` +
        (typeof km === "number" ? ` (${km.toFixed(0)} km away)` : "");
      chipKeys = ["txn_state", "home_state", "geographic_distance_from_home_km"];
      break;
    }

    case "duplicate_transaction": {
      // Pipeline emits: dup_count, txn_ids, first_at, last_at, total_myr.
      const c = e.dup_count ?? e.duplicate_count ?? e.count;
      const txn_ids = Array.isArray(e.txn_ids) ? (e.txn_ids as unknown[]) : [];
      summary =
        `${fmtNum(c, 0)} duplicates` +
        (txn_ids.length > 0 ? ` (${fmtList(txn_ids, 2)})` : "");
      chipKeys = ["dup_count", "total_myr", "first_at", "last_at", "txn_ids"];
      break;
    }

    case "termination_fee_check": {
      const fee = e.charged_fee ?? e.amount ?? e.total_myr;
      const expected = e.expected_fee;
      summary =
        `termination fee ${fmtMyrCompact(fee)}` +
        (expected != null ? ` vs expected ${fmtMyrCompact(expected)}` : "");
      chipKeys = ["charged_fee", "expected_fee", "total_myr"];
      break;
    }

    case "proration_check": {
      const billed = e.billed_amount ?? e.proration_amount_myr;
      const expected = e.expected_proration ?? e.expected_proration_myr;
      summary = `prorated ${fmtMyrCompact(billed)} vs expected ${fmtMyrCompact(expected)}`;
      chipKeys = [
        "billed_amount",
        "expected_proration",
        "proration_amount_myr",
        "expected_proration_myr"
      ];
      break;
    }

    case "double_charge_multi_code": {
      // Pipeline emits: txn_count, txn_ids, charge_codes, amounts.
      const codes = Array.isArray(e.charge_codes) ? (e.charge_codes as unknown[]) : [];
      summary =
        codes.length > 0
          ? `same service charged via ${codes.length} codes: ${fmtList(codes, 3)}`
          : "same logical service charged twice";
      chipKeys = ["charge_codes", "txn_count", "amounts", "txn_ids"];
      break;
    }

    case "unearned_earned_segregation": {
      const earned = e.earned_amount_myr;
      const unearned = e.unearned_amount_myr;
      summary = `earned ${fmtMyrCompact(earned)} / unearned ${fmtMyrCompact(unearned)}`;
      chipKeys = ["earned_amount_myr", "unearned_amount_myr", "total_myr"];
      break;
    }

    default: {
      // Unknown rule_type — pick the first non-noisy scalar/list fields.
      const usable = Object.entries(e).filter(
        ([k, v]) => !_NOISY_KEYS.has(k) && !k.startsWith("_") && v != null && typeof v !== "object"
      );
      summary = usable
        .slice(0, 2)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(", ");
      chipKeys = usable.map(([k]) => k).slice(0, 5);
    }
  }

  return { summary, chips: buildChips(e, chipKeys) };
}
