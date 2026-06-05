// Tiny typed fetch client — no third-party dep, just fetch + JSON.
import type {
  AssistResponse,
  BeforeAfter,
  Customer360,
  CustomerRecommendations,
  FeatureVector,
  QuarantineCase,
  Rule
} from "./types";
import { inspector, type InspectorPayload } from "./components/inspector/stores/inspector.svelte";

const BASE = ""; // proxied to backend via vite

export interface ReqOpts {
  /**
   * When set, appends `inspect=true` to the URL. If the response is the
   * wrapped envelope `{ data, _inspector }`, the wrapper is captured into
   * the inspector store and the inner `data` is returned to the caller.
   * Plain responses pass through unchanged.
   */
  inspect?: boolean;
}

function appendInspect(path: string): string {
  return path.includes("?") ? `${path}&inspect=true` : `${path}?inspect=true`;
}

async function jsonReq<T>(path: string, init?: RequestInit, opts?: ReqOpts): Promise<T> {
  const url = opts?.inspect ? appendInspect(path) : path;
  const res = await fetch(BASE + url, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body?.error?.message ?? body?.detail ?? msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const body = await res.json();
  if (opts?.inspect && body && typeof body === "object" && "_inspector" in body && "data" in body) {
    const env = body as { data: T; _inspector: InspectorPayload };
    try {
      inspector.setPayload(env._inspector);
    } catch {
      // store update should never break the page render
    }
    return env.data;
  }
  return body as T;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// --- Customers --------------------------------------------------------
export const customersApi = {
  list: (skip = 0, limit = 50) =>
    jsonReq<{ items: Customer360[]; skip: number; limit: number }>(
      `/api/customers?skip=${skip}&limit=${limit}`
    ),
  search: (q: string, limit = 20) =>
    jsonReq<{ items: Customer360[] }>(
      `/api/customers/search?q=${encodeURIComponent(q)}&limit=${limit}`
    ),
  get: (id: string, simulate_crm_lag = false) =>
    jsonReq<Customer360>(
      `/api/customers/${encodeURIComponent(id)}?simulate_crm_lag=${simulate_crm_lag}`
    ),
  recommendations: (id: string) =>
    jsonReq<CustomerRecommendations>(
      `/api/customers/${encodeURIComponent(id)}/recommendations`
    )
};

// --- Quarantine -------------------------------------------------------
export interface RelatedCaseRow {
  case_id: string;
  rule_name?: string | null;
  rule_type?: string | null;
  severity?: string | null;
  status?: string | null;
  disposition?: string | null;
  created_at?: string | null;
  resolved_at?: string | null;
  amount?: number | null;
  transaction_id?: string | null;
}

export interface RelatedCustomerCases {
  open: RelatedCaseRow[];
  history: RelatedCaseRow[];
}

export const quarantineApi = {
  list: (params: {
    status?: string;
    severity?: string;
    rule_type?: string;
    agent_reviewed?: boolean;
    skip?: number;
    limit?: number;
  } = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === "") continue;
      qs.set(k, String(v));
    }
    return jsonReq<{ items: QuarantineCase[]; total: number; skip: number; limit: number }>(
      `/api/quarantine/cases?${qs}`
    );
  },
  get: (id: string, opts?: ReqOpts) =>
    jsonReq<QuarantineCase>(`/api/quarantine/cases/${encodeURIComponent(id)}`, undefined, opts),
  disposition: (id: string, body: { disposition: string; analyst_id: string; analyst_notes?: string }) =>
    jsonReq<QuarantineCase>(`/api/quarantine/cases/${encodeURIComponent(id)}/disposition`, {
      method: "POST",
      body: JSON.stringify(body)
    }),
  assist: (id: string, k = 5, threshold = 0.7, opts?: ReqOpts) =>
    jsonReq<AssistResponse>(
      `/api/quarantine/cases/${encodeURIComponent(id)}/assist?k=${k}&threshold=${threshold}`,
      { method: "POST" },
      opts
    ),
  aiAssist: (id: string, force = false, opts?: ReqOpts) =>
    jsonReq<AssistResponse>(
      `/api/quarantine/cases/${encodeURIComponent(id)}/ai-assist?force=${force}`,
      { method: "POST" },
      opts
    ),
  relatedCustomer: (id: string, limit = 10) =>
    jsonReq<RelatedCustomerCases>(
      `/api/quarantine/cases/${encodeURIComponent(id)}/related-customer?limit=${limit}`
    )
};

// --- Before/after panel (B.7) ----------------------------------------
export const beforeAfterApi = {
  get: (caseId: string) =>
    jsonReq<BeforeAfter>(
      `/api/cases/${encodeURIComponent(caseId)}/before-after`
    )
};

// --- Rules ------------------------------------------------------------
export const rulesApi = {
  list: (opts?: ReqOpts) => jsonReq<{ items: Rule[] }>("/api/rules", undefined, opts),
  get: (id: string) => jsonReq<Rule>(`/api/rules/${encodeURIComponent(id)}`),
  setMode: (id: string, mode: string) =>
    jsonReq<Rule>(`/api/rules/${encodeURIComponent(id)}/mode`, {
      method: "POST",
      body: JSON.stringify({ mode })
    }),
  test: (rule_type: string, parameters: Record<string, unknown>, sample_size = 1000) =>
    jsonReq<{
      rule_type: string;
      sample_size: number;
      hit_count: number;
      hit_rate: number;
      hits: unknown[];
    }>(`/api/rules/test`, {
      method: "POST",
      body: JSON.stringify({ rule_type, parameters, sample_size })
    })
};

// --- Features ---------------------------------------------------------
export interface FeatureDriftItem {
  feature_name: string;
  measured_at: string;
  severity: "none" | "watch" | "warn" | "alert";
  ks_statistic: number;
  p_value: number;
  current: { n: number; mean: number; std: number };
  baseline: { n: number; mean: number; std: number };
}

export const featuresApi = {
  freshness: () =>
    jsonReq<{
      sampled: number;
      median_lag_seconds: number | null;
      p95_lag_seconds: number | null;
      max_lag_seconds: number | null;
      fresh_share?: number;
    }>("/api/features/freshness"),
  drift: (top = 10) =>
    jsonReq<{ items: FeatureDriftItem[]; count: number }>(
      `/api/features/drift?top=${top}`
    ),
  get: (id: string) => jsonReq<FeatureVector>(`/api/features/${encodeURIComponent(id)}`)
};

// --- System metrics (Phase B.3 burst tile) ---------------------------
export interface SystemMetricSample {
  recorded_at: string;
  mode: "steady" | "burst" | "idle";
  burst_run_id: string | null;
  observed_tps: number;
  p50_ms_ingest: number;
  p99_ms_ingest: number;
  quarantine_per_sec: number;
  rule_eval_p99_ms: number;
  txns_in_window: number;
  cases_in_window: number;
}

export const systemMetricsApi = {
  recent: (minutes = 60) =>
    jsonReq<{
      since: string;
      samples: SystemMetricSample[];
      burst_run_ids: string[];
      current_mode: "steady" | "burst" | "idle";
    }>(`/api/system-metrics?minutes=${minutes}`)
};

// ---------------- H1: Customer refresh & analytics (PR-13) ----------------
import type {
  TransactionPattern,
  EmbeddingStatus,
  RefreshResponse,
  BatchRefreshResult,
  MetricsRefreshResponse
} from "./types";

export const customersRefreshApi = {
  refresh360: (id: string, force = false) =>
    jsonReq<RefreshResponse>(
      `/api/customers/${encodeURIComponent(id)}/refresh-360?force=${force}`,
      { method: "POST" }
    ),
  batchRefresh360: (customer_ids: string[], force = false) =>
    jsonReq<BatchRefreshResult>(`/api/customers/batch-refresh-360`, {
      method: "POST",
      body: JSON.stringify({ customer_ids, force })
    }),
  transactionPattern: (id: string, days = 30) =>
    jsonReq<TransactionPattern>(
      `/api/customers/${encodeURIComponent(id)}/transaction-pattern?days=${days}`
    ),
  embeddingStatus: (id: string) =>
    jsonReq<EmbeddingStatus>(
      `/api/customers/${encodeURIComponent(id)}/embedding-status`
    ),
  metricsRefresh: (id: string, force = false) =>
    jsonReq<MetricsRefreshResponse>(
      `/api/customers/${encodeURIComponent(id)}/metrics/refresh?force=${force}`,
      { method: "POST" }
    ),
  batchMetricsRefresh: (customer_ids: string[], force = false) =>
    jsonReq<BatchRefreshResult>(`/api/customers/metrics/batch-refresh`, {
      method: "POST",
      body: JSON.stringify({ customer_ids, force })
    })
};

// ---------------- H2: Drift detail & impact (PR-13) ----------------
import type {
  DriftStatus,
  DriftImpact,
  DriftSnapshot,
  InvestigateActionPayload,
  InvestigateActionResult
} from "./types";

export const driftApi = {
  driftStatus: (name: string) =>
    jsonReq<DriftStatus>(
      `/api/features/${encodeURIComponent(name)}/drift-status`
    ),
  impactAnalysis: (name: string) =>
    jsonReq<DriftImpact>(
      `/api/features/${encodeURIComponent(name)}/impact-analysis`
    ),
  driftSnapshot: (names: string[]) => {
    const capped = names.slice(0, 20);
    const qs = capped.map((n) => encodeURIComponent(n)).join(",");
    return jsonReq<DriftSnapshot>(`/api/features/drift-snapshot?names=${qs}`);
  },
  investigateAction: (name: string, payload: InvestigateActionPayload) =>
    jsonReq<InvestigateActionResult>(
      `/api/features/${encodeURIComponent(name)}/investigate-action`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    )
};

// ---------------- H3: Agent trace & batch assist (PR-13) ----------------
import type { AgentTrace, BatchAssistResult } from "./types";

export const quarantineAssistApi = {
  assistTrace: (caseId: string) =>
    jsonReq<AgentTrace>(
      `/api/quarantine/cases/${encodeURIComponent(caseId)}/ai-assist/trace`
    ),
  batchAiAssist: (case_ids: string[], force = false) =>
    jsonReq<BatchAssistResult>(`/api/quarantine/cases/batch-ai-assist`, {
      method: "POST",
      body: JSON.stringify({ case_ids, force })
    })
};

// ---------------- H4: Burst metrics (PR-13) ----------------
import type { BurstStatus } from "./types";

export const metricsApi = {
  /**
   * Fetch the burst-mode status envelope. When `run_id` is empty/undefined
   * the backend resolves the latest burst run (or returns an empty
   * envelope if none has ever been recorded). `limit` is clamped on the
   * server to 1..720 — we still validate client-side for a sane URL.
   */
  burst: (params: { run_id?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.run_id && params.run_id.trim() !== "") {
      qs.set("run_id", params.run_id.trim());
    }
    const lim = Math.max(1, Math.min(720, Number(params.limit ?? 240)));
    qs.set("limit", String(lim));
    return jsonReq<BurstStatus>(`/api/metrics/burst?${qs}`);
  }
};

// ---------------- V3: Typed customer profile (PR-FE-1) ----------------
import type { CustomerV3, BillCycle, AtlasIndexHealth } from "./types";

/**
 * Typed V3 customer profile endpoints. The backend exposes a single
 * `GET /api/customers/{id}/profile` route that dispatches by
 * `customer_type`; the residential/commercial helpers are present so
 * route components can express intent and we can flip to dedicated
 * backend routes without touching call sites. If a typed route is not
 * yet served, the call surfaces a 404 ApiError and the caller is
 * expected to fall back to `.profile()`.
 */
export const customersV3Api = {
  profile: (id: string, opts?: ReqOpts) =>
    jsonReq<CustomerV3>(`/api/customers/${encodeURIComponent(id)}/profile`, undefined, opts),
  residential: (id: string, opts?: ReqOpts) =>
    jsonReq<CustomerV3>(`/api/customers/residential/${encodeURIComponent(id)}`, undefined, opts),
  commercial: (id: string, opts?: ReqOpts) =>
    jsonReq<CustomerV3>(`/api/customers/commercial/${encodeURIComponent(id)}`, undefined, opts),
  outlets: (parentId: string, skip = 0, limit = 50, opts?: ReqOpts) =>
    jsonReq<{ items: CustomerV3[]; total?: number; skip?: number; limit?: number }>(
      `/api/customers/commercial/${encodeURIComponent(parentId)}/outlets?skip=${skip}&limit=${limit}`,
      undefined,
      opts
    ),
  /**
   * AutoEmbed semantic search across customer documents. Backed by
   * `$vectorSearch` on the `customers_*` collections. When the endpoint
   * is not served the caller catches the resulting ApiError and renders
   * an empty state.
   */
  search: (
    q: string,
    params: {
      limit?: number;
      customer_type?: "residential" | "commercial";
      tier?: string;
      entity?: string;
      state?: string;
    } = {},
    opts?: ReqOpts
  ) => {
    const qs = new URLSearchParams();
    qs.set("q", q);
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.customer_type) qs.set("filter[customer_type]", params.customer_type);
    if (params.tier) qs.set("filter[tier]", params.tier);
    if (params.entity) qs.set("filter[entity]", params.entity);
    if (params.state) qs.set("filter[state]", params.state);
    return jsonReq<{ items: CustomerSearchHit[] }>(
      `/api/customers/search?${qs}`,
      undefined,
      opts
    );
  }
};

// V3 search hit shape — minimal so the search page can render without
// requiring a full CustomerV3 from the backend.
export interface CustomerSearchHit {
  customer_id: string;
  customer_type: "residential" | "commercial";
  name: string;
  tier?: string;
  entities?: EntityKey[];
  state?: string;
  score: number; // vectorSearchScore
  summary?: string; // derived from embed_source.text
}

// ---------------- V3: Bill cycles (PR-FE-1) ----------------
export const billCyclesApi = {
  get: (cycleId: string, opts?: ReqOpts) =>
    jsonReq<BillCycle>(`/api/bill-cycles/${encodeURIComponent(cycleId)}`, undefined, opts),
  list: (params: { customer_id?: string; skip?: number; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params))
      if (v !== undefined && v !== "") qs.set(k, String(v));
    return jsonReq<{ items: BillCycle[]; skip?: number; limit?: number }>(
      `/api/bill-cycles?${qs}`
    );
  }
};

// ---------------- V3: Atlas index health (PR-FE-1) ----------------
export const atlasIndexHealthApi = {
  get: () => jsonReq<AtlasIndexHealth>(`/api/_atlas/index-health`)
};

// ---------------- V3: Dashboard rollups ----------------
import type { EntityKey } from "./types";

// Net new revenue (rolling, by entity, last `hours`).
export interface RevenueTrendPoint {
  ts: string;
  value_myr: number;
}
export interface NetNewRevenueRow {
  entity: EntityKey | string;
  total_myr: number;
  count: number;
  trend: RevenueTrendPoint[];
}
export interface NetNewRevenueSummary {
  total_myr: number;
  count: number;
  window_hours: number;
  bucket_unit: "minute" | "hour" | string;
  rows: NetNewRevenueRow[];
  generated_at?: string;
}

export interface EntityHealthRow {
  entity: EntityKey;
  subscriber_count: number;
  mrr_myr: number;
  churn_band: "low" | "medium" | "high" | string;
}
export interface EntityHealthSummary {
  rows: EntityHealthRow[];
  generated_at?: string;
}

export interface LatestFeaturesItem {
  feature_name: string;
  value: number | string;
  unit?: string;
  updated_at?: string;
}
export interface LatestFeaturesSummary {
  items: LatestFeaturesItem[];
  generated_at?: string;
}

export interface TxnRateBucket {
  ts: string;
  count: number;
  total_myr: number;
  quarantined: number;
}
export interface TxnRateSummary {
  buckets: TxnRateBucket[];
  window_minutes: number;
  bucket_unit: "minute" | "hour" | string;
  total_count: number;
  total_myr: number;
  generated_at?: string;
}

export const dashboardApi = {
  netNewRevenue: (hours = 1, opts?: ReqOpts) =>
    jsonReq<NetNewRevenueSummary>(
      `/api/_dashboard/net-new-revenue?hours=${hours}`, undefined, opts
    ),
  entityHealth: (opts?: ReqOpts) =>
    jsonReq<EntityHealthSummary>(`/api/_dashboard/entity-health`, undefined, opts),
  latestFeatures: (opts?: ReqOpts) =>
    jsonReq<LatestFeaturesSummary>(`/api/_dashboard/latest-features`, undefined, opts),
  transactionRate: (minutes = 60) =>
    jsonReq<TxnRateSummary>(`/api/_dashboard/transaction-rate?minutes=${minutes}`)
};

// ---------------- PR-FE-3: Cases-by-severity + extras ----------------
export interface SeverityCasesRow {
  severity: "low" | "medium" | "high" | "critical" | string;
  cases_count: number;
  cases_per_day: number;
  open_cases?: number;
}
export interface CasesBySeveritySummary {
  rows: SeverityCasesRow[];
  window_hours: number;
  generated_at?: string;
}
export interface TopDriftItem {
  feature_name: string;
  severity: "none" | "watch" | "warn" | "alert";
  ks_statistic: number;
  affected_consumers_count: number;
}
export interface WhatChangedItem {
  kind: "drift_alert" | "sla_breach" | "campaign_converted" | string;
  ts: string;
  summary: string;
  ref?: string;
}

export const polishApi = {
  casesBySeverity: (hours = 24) =>
    jsonReq<CasesBySeveritySummary>(
      `/api/_dashboard/cases-by-severity?hours=${hours}`
    ),
  resolutionVelocity: () =>
    jsonReq<{ median_minutes: number | null; p95_minutes: number | null; sample: number }>(
      `/api/_dashboard/resolution-velocity`
    ),
  topDrift: (top = 5) =>
    jsonReq<{ items: TopDriftItem[] }>(`/api/_dashboard/top-drift?top=${top}`),
  whatChanged: (hours = 24) =>
    jsonReq<{ items: WhatChangedItem[] }>(`/api/_dashboard/what-changed?hours=${hours}`)
};

// ---------------- PR-FE-3: Drift history (for the trend chart) ----------------
export interface DriftHistoryPoint {
  measured_at: string;
  ks_statistic: number;
  severity: "none" | "watch" | "warn" | "alert";
}
export const driftHistoryApi = {
  history: (name: string, days = 30) =>
    jsonReq<{ items: DriftHistoryPoint[] }>(
      `/api/features/${encodeURIComponent(name)}/history?days=${days}`
    )
};

// ---------------- PR-FE-3: iForest score endpoint ----------------
export interface IForestScore {
  customer_id: string;
  model_version: string;
  score: number;
  scored_at: string;
  cluster?: string;
  cluster_distribution?: { bucket: string; count: number }[];
}
export const iforestApi = {
  get: (id: string) =>
    jsonReq<IForestScore>(`/api/customers/${encodeURIComponent(id)}/iforest-score`),
  rescore: (id: string) =>
    jsonReq<IForestScore>(
      `/api/customers/${encodeURIComponent(id)}/iforest-score`,
      { method: "POST" }
    )
};
