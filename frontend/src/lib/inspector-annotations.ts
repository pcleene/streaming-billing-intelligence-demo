// PR-FE-2: Inspector annotation library. Maps top-level document paths
// to MongoDB pattern tags + ADR references. The Result tab consults
// this map to decorate keys with chips.

export type AnnotationPattern =
  | "autoembed"
  | "ext_ref"
  | "subset"
  | "computed"
  | "bucket";

export interface Annotation {
  pattern: AnnotationPattern;
  ref: string; // ADR id, e.g. "ADR-011"
  /** One-line description shown in the hover popover. */
  blurb?: string;
}

export const ANNOTATIONS: Record<string, Annotation> = {
  "embed_source.text": {
    pattern: "autoembed",
    ref: "ADR-032",
    blurb: "AutoEmbed-indexed via voyage-4-large at write time."
  },
  embed_source: {
    pattern: "autoembed",
    ref: "ADR-032",
    blurb: "AutoEmbed source object — text becomes a vector at insert."
  },
  recent_transactions: {
    pattern: "subset",
    ref: "ADR-011",
    blurb: "Subset Pattern — capped preview of the last 50 transactions."
  },
  recent_transactions_full: {
    pattern: "subset",
    ref: "ADR-011",
    blurb: "Subset Pattern — full window embedded for the SCV page."
  },
  open_cases: {
    pattern: "subset",
    ref: "ADR-011",
    blurb: "Embedded queue (no $lookup) — Subset Pattern."
  },
  latest_features: {
    pattern: "computed",
    ref: "ADR-013",
    blurb: "Computed Pattern — sub-minute rolling feature view."
  },
  current_cycle: {
    pattern: "computed",
    ref: "ADR-023",
    blurb: "Computed Pattern — materialised bill cycle snapshot."
  },
  cross_entity_metrics: {
    pattern: "computed",
    ref: "ADR-028",
    blurb: "Computed Pattern — cross-entity rollup with 12-month trends."
  },
  recent_transactions_buckets: {
    pattern: "bucket",
    ref: "ADR-020",
    blurb: "Bucket Pattern — time-windowed transactions."
  },
  customer_summary: {
    pattern: "ext_ref",
    ref: "ADR-022",
    blurb: "Extended Reference — frozen at write to avoid joins."
  },
  transaction_summary: {
    pattern: "ext_ref",
    ref: "ADR-026",
    blurb: "Extended Reference — embedded merchant snapshot."
  }
};

export const PATTERN_LABEL: Record<AnnotationPattern, string> = {
  autoembed: "AutoEmbed",
  ext_ref: "Extended Reference",
  subset: "Subset Pattern",
  computed: "Computed Pattern",
  bucket: "Bucket Pattern"
};

export const PATTERN_TINT: Record<AnnotationPattern, string> = {
  autoembed: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
  ext_ref: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  subset: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  computed: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  bucket: "bg-rose-500/15 text-rose-300 border-rose-500/30"
};

export function annotationFor(path: string): Annotation | undefined {
  if (ANNOTATIONS[path]) return ANNOTATIONS[path];
  // Allow path prefix match for nested keys (e.g. customer_summary.* tags
  // the whole sub-tree).
  for (const k of Object.keys(ANNOTATIONS)) {
    if (path === k || path.startsWith(k + ".")) return ANNOTATIONS[k];
  }
  return undefined;
}
