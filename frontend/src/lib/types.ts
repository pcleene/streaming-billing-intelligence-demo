// Backend response types — kept loose because Pydantic emits canonical JSON
// and we don't share schema across the wire.

export type Severity = "low" | "medium" | "high";
export type CaseStatus = "open" | "under_review" | "resolved" | "dismissed";
export type Disposition =
  | "true_positive"
  | "false_positive"
  | "escalate"
  | "needs_more_info"
  | null;
export type RuleMode = "shadow" | "active" | "disabled";

export interface Subscription {
  package_id: string;
  package_name: string;
  monthly_price_myr: number;
  status: string;
  started_at: string;
}

export interface Address {
  state: string;
  city?: string;
  street?: string;
  postcode?: string;
  country?: string;
}

export interface RecentTransactionEmbed {
  transaction_id: string;
  amount: number;
  transaction_type: string;
  timestamp: string;
}

export interface FullTransaction extends RecentTransactionEmbed {
  customer_id: string;
  merchant_id: string;
  discount_amount?: number;
  location?: { state: string; country: string };
}

export interface CrmLagInfo {
  simulated: boolean;
  lag_hours: number;
  snapshot_at: string;
  note?: string;
}

export type ChurnRiskBand = "low" | "medium" | "high";
export type NboOfferType =
  | "upgrade"
  | "addon"
  | "retention_discount"
  | "winback"
  | "loyalty_perk";

export interface ChurnRisk {
  band: ChurnRiskBand;
  score: number;
  drivers: string[];
}

export interface NextBestOffer {
  offer_id: string;
  offer_type: NboOfferType;
  title: string;
  rationale: string;
  expected_uplift_myr: number;
  priority: number;
}

export interface CustomerRecommendations {
  computed_at: string;
  churn_risk: ChurnRisk;
  next_best_offers: NextBestOffer[];
}

export interface Customer360 {
  customer_id: string;
  name: string;
  email: string;
  phone?: string;
  segment: string;
  address: Address;
  subscriptions: Subscription[];
  active_promotions?: { promo_code: string; discount_pct: number; valid_until: string }[];
  entitlements?: { content_id: string; granted_at: string; kind: string }[];
  recent_transactions?: RecentTransactionEmbed[];
  recent_transactions_full?: FullTransaction[];
  open_cases?: QuarantineCase[];
  features?: FeatureVector | null;
  crm_lag?: CrmLagInfo;
  lifetime_quarantine_count?: number;
  recommendations?: CustomerRecommendations | null;
}

export interface RuleHit {
  rule_type: string;
  rule_name: string;
  fired_at: string;
  score?: number;
  evidence?: Record<string, unknown>;
}

export interface QuarantineCase {
  case_id: string;
  customer_id: string;
  amount?: number;
  severity: Severity;
  status: CaseStatus;
  disposition?: Disposition;
  rules_triggered: RuleHit[];
  created_at: string;
  resolved_at?: string;
  analyst_notes?: string;
  customer_snapshot?: { segment?: string; state?: string };
}

export interface FeatureVector {
  customer_id: string;
  txn_count_5m?: number;
  amount_sum_5m?: number;
  discount_sum_5m?: number;
  txn_count_total?: number;
  updated_at?: string;
  last_txn_at?: string;
}

export interface Rule {
  rule_id: string;
  name: string;
  rule_type: string;
  parameters: Record<string, unknown>;
  severity: Severity;
  mode: RuleMode;
  enabled: boolean;
  hit_count: number;
  created_at: string;
  updated_at: string;
}

export interface AssistResponse {
  case_id: string;
  similar_cases: Array<QuarantineCase & { score?: number; embedding_text?: string }>;
  assist: {
    summary: string;
    likelihood: string;
    confidence: number;
    rationale: string[];
    recommended_steps: string[];
    references: { case_id: string; disposition: string; score?: number; why_relevant?: string }[];
  };
  degraded?: boolean;
  reason?: string;
}

export interface MetricsTick {
  transactions_per_sec: number;
  quarantine_per_sec: number;
  p99_eval_ms: number;
  p50_eval_ms: number;
  ts: number;
}

// Compact projection emitted by the transaction consumer on every
// accepted Kafka event — drives the dashboard's "Live transactions"
// scrolling feed. Both the in-process producer (transaction_consumer)
// and the API-side change-stream tail (sse_change_stream_tail) emit
// this shape so the wire contract stays stable across topologies.
export interface LiveTxn {
  transaction_id?: string | null;
  customer_id?: string | null;
  amount?: number | null;
  charge_code?: string | null;
  status?: string | null;
  merchant?: string | null;
  entity?: string | null;
  /** Event time from the producer/ASP (ISO-8601). Preferred for display. */
  timestamp?: string | null;
  /** Persistence time (ISO-8601) — fallback when `timestamp` is absent. */
  created_at?: string | null;
  source_partition?: number | null;
  source_offset?: number | null;
  // Forward compat for any extra fields the backend later includes.
  [key: string]: unknown;
}

export type ProjectionSource = "crm_snapshot" | "derived_lag" | "live";

export interface BeforeAfterProjection {
  source: ProjectionSource;
  snapshot_at: string | null;
  customer_summary: Record<string, unknown>;
  open_case_count: number;
  recent_transaction_count: number;
  has_ai_assist: boolean;
  ai_assist_summary: string | null;
  rules_visible: string[];
  notes: string[];
}

export interface BeforeAfter {
  case_id: string;
  customer_id: string;
  generated_at: string;
  before: BeforeAfterProjection;
  after: BeforeAfterProjection;
  would_quarantine: boolean;
  auto_resolution_latency_seconds: number | null;
}

// ---------------- H1: Customer refresh & analytics (PR-13) ----------------
export interface TransactionPatternChargeCode {
  charge_code: string;
  count: number;
}

export interface TransactionPattern {
  customer_id?: string;
  days?: number;
  txn_count: number;
  amount_mean_myr: number;
  amount_stddev_myr: number;
  top_charge_codes: TransactionPatternChargeCode[];
  first_txn_at: string | null;
  last_txn_at: string | null;
}

export interface EmbeddingStatus {
  customer_id: string;
  has_embedding: boolean;
  dim: number | null;
  generated_at: string | null;
  age_seconds: number | null;
  is_stale: boolean;
}

export interface RefreshResponse {
  customer_id: string;
  refreshed: boolean;
  skipped_reason?: string | null;
  computed_at?: string | null;
  source?: string | null;
  // Allow forward compatibility; backend may include extra fields.
  [key: string]: unknown;
}

export interface BatchRefreshResult {
  total: number;
  refreshed: number;
  skipped: number;
  failed: number;
  results: RefreshResponse[];
}

export interface MetricsRefreshResponse {
  customer_id: string;
  computed: boolean;
  skipped_reason?: string | null;
  cross_entity_metrics?: Record<string, unknown> | null;
}

// ---------------- H2: Drift detail & impact (PR-13) ----------------
export type DriftSeverity = "none" | "watch" | "warn" | "alert" | string;

/**
 * One severity transition. The runtime detector emits `{at, from, to}`;
 * older seed fixtures emitted `{at, severity}` (where `severity` is the
 * post-transition value). Both are accepted on the wire — the FE picks
 * whichever fields are present.
 */
export interface SeverityProgressionStep {
  at: string;
  from?: DriftSeverity;
  to?: DriftSeverity;
  severity?: DriftSeverity;
}

/** Distribution stats block emitted by the drift detector for current
 *  and baseline windows (`_stats` shape in `feature_drift_detector.py`). */
export interface DriftStatsBlock {
  n: number;
  mean: number;
  std: number;
  min?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  p99?: number;
  max?: number;
}

/** Lineage / version metadata. Two shapes exist on the wire:
 *  - the runtime detector emits a flat list of model versions, e.g.
 *    `["churn_model_v2@2026-03-01"]`;
 *  - the seed emits a dict with versions and an embedded `models` list.
 *  The FE renders whichever fields are present. */
export type DriftModelLineage =
  | string[]
  | {
      feature_set_version?: string;
      code_version?: string;
      models?: string[];
    };

export interface DriftStatus {
  feature_name: string;
  ks_statistic: number;
  severity: DriftSeverity;
  drift_detected: boolean;
  recommended_action: string;
  severity_progression: SeverityProgressionStep[];
  last_observed_at: string | null;
  // Optional richer fields — present when the runtime detector or the
  // PR-14-revised seed populated them; absent on legacy fixtures.
  p_value?: number;
  current?: DriftStatsBlock;
  baseline?: DriftStatsBlock;
  sample_size_current?: number;
  sample_size_baseline?: number;
  model_lineage?: DriftModelLineage;
  measured_at?: string;
  // Allow forward compatibility for additional backend fields.
  [key: string]: unknown;
}

export interface AffectedConsumer {
  type: "rule" | "model";
  name: string;
  last_seen: string | null;
}

export interface BlastRadius {
  rule_count: number;
  model_count: number;
}

export interface DriftImpact {
  feature_name: string;
  drift: DriftStatus;
  affected_consumers: AffectedConsumer[];
  recommended_action: string;
  blast_radius: BlastRadius;
}

export interface DriftSnapshot {
  by_feature: Record<string, DriftStatus>;
  any_drift_detected: boolean;
}

export type InvestigateAction = "acknowledge" | "snooze" | "escalate";

export interface InvestigateActionPayload {
  action: InvestigateAction;
  note?: string;
  snooze_until?: string;
}

export interface InvestigateActionResult {
  feature_name: string;
  action: InvestigateAction;
  recorded_at: string;
  note?: string | null;
  snooze_until?: string | null;
  // Allow forward compatibility.
  [key: string]: unknown;
}

// ---------------- H3: Agent trace & batch assist (PR-13) ----------------
export interface AgentTraceEntry {
  node?: string;
  started_at?: string;
  duration_ms?: number | null;
  error?: string | null;
  // Permissive: backend node states may include additional fields.
  [k: string]: unknown;
}

export interface AgentTraceSummary {
  path_taken: string[];
  nodes_run: number;
  error_count: number;
  total_duration_ms: number | null;
}

export interface AgentTrace {
  case_id: string;
  has_trace: boolean;
  trace: AgentTraceEntry[];
  summary: AgentTraceSummary;
}

export interface BatchAssistRequest {
  case_ids: string[];
  force?: boolean;
}

export interface BatchAssistError {
  case_id: string;
  reason: string;
}

export interface BatchAssistResult {
  requested: number;
  generated: number;
  skipped: number;
  errors: BatchAssistError[];
}

// ---------------- H4: Burst metrics (PR-13) ----------------
// Backend envelope is intentionally permissive — the dual-shape contract
// keeps both legacy keys (peak_tps, p99_rule_eval_ms_max, row_count) and
// PR-11 spec keys (peak_observed_tps, peak_p99_ms, sample_count) alive on
// the same object. Treat all fields as optional on the wire.

export interface BurstSample {
  recorded_at?: string | null;
  mode?: "steady" | "burst" | "idle" | string | null;
  burst_run_id?: string | null;
  observed_tps?: number | null;
  p50_ms_ingest?: number | null;
  p99_ms_ingest?: number | null;
  quarantine_per_sec?: number | null;
  rule_eval_p99_ms?: number | null;
  txns_in_window?: number | null;
  cases_in_window?: number | null;
  eval_queue_depth?: number | null;
  // Forward compat for any future fields the recorder adds.
  [key: string]: unknown;
}

export interface BurstSummaryStats {
  // PR-11 keys (preferred)
  sample_count?: number;
  peak_observed_tps?: number;
  peak_p99_ms?: number;
  rule_eval_p99_threshold_breaches?: number;
  duration_seconds?: number;
  // Legacy keys (kept for completeness)
  row_count?: number;
  peak_tps?: number;
  mean_tps?: number;
  p99_rule_eval_ms_max?: number;
  target_tps_compliance?: number;
  started_at?: string | null;
  ended_at?: string | null;
  [key: string]: unknown;
}

export interface BurstStatus {
  run_id: string | null;
  active: boolean;
  started_at?: string | null;
  ended_at?: string | null;
  rows?: BurstSample[];
  samples?: BurstSample[];
  summary?: BurstSummaryStats;
  // Forward compat — older deployments may inline summary fields at top level.
  [key: string]: unknown;
}

// ---------------- V3: Customer 360 (PR-FE-1) ----------------
// Mirrors the Customer360Service / GET /api/customers/{id}/profile shape
// emitted by the V4 backend. Permissive `[key: string]: unknown` on every
// open-ended container so the wire remains forward compatible.

export type CustomerType = "residential" | "commercial";

export type EntityKey =
  | "acme_paytv"
  | "acme_streaming"
  | "acme_broadband"
  | "acme_prepaid"
  | "acme_business"
  | "acme_cards";

export type CustomerTier = "bronze" | "silver" | "gold" | "platinum";

export interface ChannelOptIn {
  channel: "email" | "sms" | "push_notification" | "whatsapp" | "acme_app_inbox" | string;
  opted_in: boolean;
  opted_in_date: string;
}

export interface CommunicationPreferences {
  preferred_language: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  preferred_contact_time: "morning" | "afternoon" | "evening" | "night" | string;
  do_not_disturb: boolean;
}

export interface ContactBlock {
  email: string;
  phone: string;
  channel_opt_ins: ChannelOptIn[];
  channel_opt_outs: string[];
  communication_preferences: CommunicationPreferences;
}

export interface GeoPoint {
  type: "Point";
  coordinates: [number, number];
}

export interface AddressBlock {
  street: string;
  city: string;
  state: string;
  postcode: string;
  location?: GeoPoint;
  [key: string]: unknown;
}

export interface UnifiedProfile {
  name: string;
  preferred_name?: string;
  ethnicity?: string;
  ic_number?: string;
  date_of_birth?: string;
  gender?: "male" | "female" | "other" | string;
  contact: ContactBlock;
  address: AddressBlock;
  [key: string]: unknown;
}

export interface EntityProfilePayTv {
  member_since: string;
  primary_package: string;
  monthly_mrr_myr: number;
  household_size?: number;
  lock_in_months_remaining?: number;
}

export interface EntityProfileStreaming {
  member_since: string;
  active_apps: string[];
  monthly_minutes_watched: number;
  ppv_count_30d: number;
}

export interface EntityProfileBroadband {
  member_since: string;
  downstream_mbps: number;
  data_cap_gb?: number | null;
}

export interface EntityProfilePrepaid {
  activated_at: string;
  prepaid_topup_count_30d?: number;
}

export interface EntityProfileBusiness {
  member_since: string;
  outlet_count: number;
  primary_package: string;
  contract_renewal_at: string;
  monthly_mrr_myr: number;
  ssm_number?: string;
}

export interface EntityProfileCards {
  tier: string;
  points_balance: number;
  last_redeemed_at?: string | null;
}

export type EntityProfiles = Partial<{
  acme_paytv: EntityProfilePayTv;
  acme_streaming: EntityProfileStreaming;
  acme_broadband: EntityProfileBroadband;
  acme_prepaid: EntityProfilePrepaid;
  acme_business: EntityProfileBusiness;
  acme_cards: EntityProfileCards;
}>;

export interface TrendPoint {
  month: string; // ISO month e.g. "2026-04"
  value: number;
}

export interface CrossEntityMetrics {
  total_ltv_myr: number;
  cross_sell_score: number;
  churn_risk: number;
  ltv_trend_12m: TrendPoint[];
  monthly_spend_trend_12m: TrendPoint[];
  viewing_hours_trend_12m?: TrendPoint[];
  ppv_count_trend_12m?: TrendPoint[];
  [key: string]: unknown;
}

export interface BrandJourneyEvent {
  entity: EntityKey | string;
  event: string;
  date: string;
  details?: Record<string, unknown>;
}

export interface SupportInteraction {
  ticket_id: string;
  date: string;
  channel: string;
  agent_id: string;
  category: string;
  subcategory: string;
  sentiment: "positive" | "neutral" | "negative" | string;
  resolution: "resolved" | "pending" | "escalated" | string;
  resolution_time_minutes: number;
  notes: string;
}

export interface MarketingInteraction {
  campaign_id: string;
  content_id: string;
  channel: string;
  sent_at: string;
  opened_at: string | null;
  clicked_at: string | null;
  converted_at: string | null;
  revenue_attributed_myr: number;
}

export interface ChannelEngagementRate {
  open_rate: number;
  ctr: number;
  conversion_rate: number;
  total_sent: number;
  last_engaged_at: string;
}

export interface InteractionHistory {
  support_interactions: SupportInteraction[];
  marketing_interactions: MarketingInteraction[];
  channel_engagement_rates: Partial<Record<string, ChannelEngagementRate>>;
}

export interface ActiveCampaign {
  campaign_id: string;
  campaign_name: string;
  enrollment_id: string;
  enrolled_date: string;
  enrolled_by: "manual" | "ml_signal" | "rule" | string;
  recommended_channel: string;
  reasoning: string;
  similar_customer_conversion_rate: number;
  expected_ltv_uplift: number;
  similar_customers_sampled: string[];
  status: "queued" | "in_flight" | "converted" | "expired" | string;
  revenue_realized_myr?: number;
}

export interface Equipment {
  equipment_id: string;
  type: "set_top_box" | "smart_card" | "decoder" | "router" | string;
  model: string;
  serial: string;
  status: "active" | "swap_requested" | "returned" | string;
  installed_at: string;
}

export interface CurrentCycle {
  cycle_id: string;
  cycle_start: string;
  cycle_end: string;
  days_remaining: number;
  expected_amount_myr: number;
  billed_amount_myr: number | null;
  variance_myr: number | null;
}

// V3 flat-root customer payload (PR-15). `unified_profile` was retired
// — identity scalars live at the document root, alongside the embedded
// `contact` and `address` blocks. `unified_profile` is kept here as an
// optional field for backward-compat with any cached response from a
// pre-PR-15 backend; new code should consume the flat-root fields.
export interface CustomerV3 {
  customer_id: string;
  customer_type: CustomerType;
  account_id: string;
  parent_account_id?: string | null;
  outlet_id?: string | null;
  is_parent_account?: boolean;
  tier: CustomerTier;
  // Flat-root identity scalars (replaces the PR-14 unified_profile block).
  name: string;
  preferred_name?: string | null;
  ethnicity?: string | null;
  ic_number?: string | null;
  date_of_birth?: string | null;
  gender?: string | null;
  marital_status?: string | null;
  household_size?: number | null;
  occupation_band?: string | null;
  contact?: ContactBlock | null;
  address?: AddressBlock | null;
  /** @deprecated PR-14 only — flat-root fields are canonical post-PR-15 */
  unified_profile?: UnifiedProfile | null;
  // Commercial-only.
  business_profile?: Record<string, unknown> | null;
  // Cross-entity & history.
  entities: EntityKey[];
  entity_profiles: EntityProfiles;
  cross_entity_metrics: CrossEntityMetrics;
  brand_journey: BrandJourneyEvent[];
  interaction_history: InteractionHistory;
  active_campaigns: ActiveCampaign[];
  recommendations: CustomerRecommendations | null;
  equipment: Equipment[];
  current_cycle?: CurrentCycle | null;
  // Operational hot-path embeds (V2/Phase A carryovers).
  subscriptions?: Array<{
    package_code?: string;
    package_name?: string;
    status?: string;
    monthly_fee_myr?: number;
    started_at?: string;
    next_billing_at?: string;
  }>;
  active_promotions?: Array<{
    promotion_code?: string;
    description?: string;
    discount_pct?: number | null;
    discount_amount_myr?: number | null;
    valid_from?: string;
    valid_to?: string;
  }>;
  entitlements?: Array<{
    content_id?: string;
    content_name?: string;
    granted_at?: string;
    expires_at?: string;
  }>;
  recent_transactions?: RecentTransactionEmbed[];
  recent_support?: Array<{
    ticket_id: string;
    summary: string;
    opened_at?: string;
    closed_at?: string | null;
    sentiment?: "positive" | "neutral" | "negative" | string;
  }>;
  open_cases?: QuarantineCase[];
  latest_features?: FeatureVector | null;
  total_monthly_value_myr?: number;
  lifetime_quarantine_count?: number;
  // AutoEmbed surface — the source text used by the customers AutoEmbed
  // index. Atlas-managed vectors are NEVER returned over the wire; only
  // the input text is exposed so analysts can see what the semantic
  // search signature looks like.
  embed_source?: { text: string } | null;
  // Forward compat for backend-only fields (e.g. iforest_score).
  iforest_score?: number | null;
  [key: string]: unknown;
}

// ---------------- V3: Bill cycles (PR-FE-1) ----------------
export interface VarianceDriver {
  driver: string;
  amount_myr: number;
  note?: string;
}

export interface BillCycleCaseRef {
  case_id: string;
  severity?: Severity | string;
  status?: CaseStatus | string;
  amount_myr?: number;
  rule_type?: string;
}

export interface BillCycle {
  cycle_id: string;
  customer_id: string;
  account_id: string;
  cycle_start: string;
  cycle_end: string;
  expected_amount_myr: number;
  billed_amount_myr: number | null;
  variance_myr: number | null;
  variance_drivers: VarianceDriver[];
  previous_cycle?: Partial<BillCycle> | null;
  associated_quarantine_cases: BillCycleCaseRef[];
  [key: string]: unknown;
}

// ---------------- V3: Atlas index health (PR-FE-1) ----------------
export type AtlasIndexState =
  | "READY"
  | "STALE_START"
  | "BUILDING"
  | "FAILED"
  | "DELETING"
  | string;

export interface AtlasIndexEntry {
  collection: string;
  index_name: string;
  state: AtlasIndexState;
  queryable: boolean;
  type?: "vectorSearch" | "search" | string;
  message?: string | null;
}

export interface AtlasIndexHealth {
  indexes: AtlasIndexEntry[];
  overall: "ready" | "syncing" | "failed" | "unknown";
  checked_at: string;
}

// V2 customer payload — kept for the legacy /api/customers/{id} callers.
// Deprecated: replace usages with CustomerV3 + GET /api/customers/{id}/profile.
/** @deprecated use CustomerV3 */
export type CustomerV2 = Customer360;
