// Per-rule_type parameter schemas. Drives dynamic form rendering in the
// Rule Studio. Mirrors the backend Pydantic discriminated union exactly.

export type FieldKind = "number" | "list_string" | "list_number";

export interface FieldDef {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  help?: string;
  default?: unknown;
}

export interface RuleSchema {
  rule_type: string;
  label: string;
  description: string;
  fields: FieldDef[];
}

export const RULE_SCHEMAS: RuleSchema[] = [
  {
    rule_type: "discount_mismatch",
    label: "Discount without active promotion",
    description: "Quarantine when discount > threshold and the customer has no active promo.",
    fields: [
      { key: "min_discount_amount_myr", label: "Minimum discount (MYR)", kind: "number", default: 5.0,
        help: "Ignore discounts below this amount (small loyalty rebates)." }
    ]
  },
  {
    rule_type: "velocity_anomaly",
    label: "Velocity burst",
    description: "More than N transactions in a short window from a customer/merchant pair.",
    fields: [
      { key: "window_seconds", label: "Window (seconds)", kind: "number", default: 300 },
      { key: "max_transactions", label: "Max transactions", kind: "number", default: 5 },
      { key: "group_by", label: "Group by", kind: "list_string", default: ["customer_id", "merchant_id"],
        help: "Comma-separated list of fields to group by." }
    ]
  },
  {
    rule_type: "amount_outlier",
    label: "Amount outlier vs customer history",
    description: "Standard-deviation outlier per customer over the lookback window.",
    fields: [
      { key: "std_dev_multiplier", label: "σ multiplier", kind: "number", default: 3.0 },
      { key: "lookback_days", label: "Lookback (days)", kind: "number", default: 90 },
      { key: "minimum_history_count", label: "Minimum history count", kind: "number", default: 10 }
    ]
  },
  {
    rule_type: "entitlement_mismatch",
    label: "PPV without entitlement",
    description: "PPV purchase whose content_id is missing from the customer's entitlements.",
    fields: []
  },
  {
    rule_type: "geographic_anomaly",
    label: "Geographic anomaly",
    description: "Transaction state ≠ customer's home state.",
    fields: []
  },
  {
    rule_type: "duplicate_transaction",
    label: "Duplicate transactions",
    description: "Two or more transactions matching the same key fields within the window.",
    fields: [
      { key: "window_seconds", label: "Window (seconds)", kind: "number", default: 60 },
      { key: "fields_to_match", label: "Fields to match", kind: "list_string",
        default: ["customer_id", "merchant_id", "amount"] }
    ]
  }
];

export function defaultParams(rule_type: string): Record<string, unknown> {
  const s = RULE_SCHEMAS.find((x) => x.rule_type === rule_type);
  if (!s) return {};
  const out: Record<string, unknown> = {};
  for (const f of s.fields) out[f.key] = f.default;
  return out;
}
