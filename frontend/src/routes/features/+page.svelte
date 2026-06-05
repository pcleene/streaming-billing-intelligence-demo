<script lang="ts">
  import { onMount } from "svelte";
  import { featuresApi, driftApi, type FeatureDriftItem } from "$lib/api";
  import type { DriftImpact, FeatureVector } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import { fmtRelative, fmtMyr } from "$lib/utils";
  import {
    ChevronDown,
    ChevronRight,
    Info,
    AlertTriangle,
    Activity,
    Search,
    Code2
  } from "lucide-svelte";

  // ─── Pipeline freshness ─────────────────────────────────────────────
  type FreshnessSnapshot = {
    sampled: number;
    median_lag_seconds: number | null;
    p95_lag_seconds: number | null;
    max_lag_seconds: number | null;
    fresh_share?: number;
    fresh_threshold_seconds?: number;
  };

  let snapshot = $state<FreshnessSnapshot | null>(null);
  let snapshotErr = $state<string | null>(null);

  async function loadFreshness() {
    snapshotErr = null;
    try {
      snapshot = await featuresApi.freshness();
    } catch (e) {
      snapshotErr = e instanceof Error ? e.message : String(e);
      snapshot = null;
    }
  }

  function humanizeSeconds(s: number | null | undefined): string {
    if (s == null) return "—";
    if (s < 1) return "<1s";
    if (s < 60) return `${Math.round(s)}s`;
    const m = Math.floor(s / 60);
    const rs = Math.round(s % 60);
    if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return rm ? `${h}h ${rm}m` : `${h}h`;
  }

  type Health = { tone: "ok" | "warn" | "danger" | "muted"; label: string; message: string };

  const pipelineHealth = $derived<Health>((() => {
    if (!snapshot)
      return { tone: "muted", label: "Loading", message: "Sampling feature documents…" };
    const median = snapshot.median_lag_seconds ?? Number.POSITIVE_INFINITY;
    const p95 = snapshot.p95_lag_seconds ?? median;
    const fresh = snapshot.fresh_share ?? 0;
    const target = snapshot.fresh_threshold_seconds ?? 60;
    if (fresh >= 0.95 && median <= target)
      return {
        tone: "ok",
        label: "Healthy",
        message: `${Math.round(fresh * 100)}% of sampled features were updated within the ${target}s SLO. Median write→read lag is ${humanizeSeconds(median)}.`
      };
    if (fresh >= 0.8 && median <= target * 2)
      return {
        tone: "warn",
        label: "Degraded",
        message: `Lag is climbing — median ${humanizeSeconds(median)}, p95 ${humanizeSeconds(p95)}. Check the change-stream feature_engineer and Spark micro-batch lag.`
      };
    return {
      tone: "danger",
      label: "Stale",
      message: `Pipeline appears stalled — only ${Math.round(fresh * 100)}% of sampled features are within the ${target}s SLO and median lag is ${humanizeSeconds(median)}. Inspect the feature_engineer worker (change-stream tail) and the Spark Structured Streaming job.`
    };
  })());

  function toneClass(t: Health["tone"]): string {
    return t === "ok"
      ? "border-ok/40 bg-ok/10 text-ok"
      : t === "warn"
        ? "border-warn/40 bg-warn/10 text-warn"
        : t === "danger"
          ? "border-danger/40 bg-danger/10 text-danger"
          : "border-border bg-bg/40 text-muted";
  }

  // ─── Drift snapshot (enriched) ──────────────────────────────────────
  let driftItems = $state<FeatureDriftItem[]>([]);
  let driftLoading = $state(true);
  let driftErr = $state<string | null>(null);
  let expanded = $state<Record<string, boolean>>({});
  let impactCache = $state<Record<string, DriftImpact | "loading" | "error">>({});

  async function loadDrift() {
    driftLoading = true;
    driftErr = null;
    try {
      const r = await featuresApi.drift(8);
      driftItems = r.items ?? [];
    } catch (e) {
      driftErr = e instanceof Error ? e.message : String(e);
      driftItems = [];
    } finally {
      driftLoading = false;
    }
  }

  function toggleExpand(name: string) {
    expanded = { ...expanded, [name]: !expanded[name] };
    if (expanded[name]) loadImpact(name);
  }

  async function loadImpact(name: string) {
    const v = impactCache[name];
    if (v && v !== "error") return;
    impactCache = { ...impactCache, [name]: "loading" };
    try {
      const r = await driftApi.impactAnalysis(name);
      impactCache = { ...impactCache, [name]: r };
    } catch {
      impactCache = { ...impactCache, [name]: "error" };
    }
  }

  // ─── Severity helpers ───────────────────────────────────────────────
  type Band = { label: string; min: number; tone: Health["tone"]; description: string };

  const SEVERITY_BANDS: Band[] = [
    {
      label: "alert",
      min: 0.4,
      tone: "danger",
      description: "Sharp distribution divergence — page on-call when downstream consumers exist."
    },
    {
      label: "warn",
      min: 0.2,
      tone: "warn",
      description: "Notable shift — investigate before retraining or tightening rule thresholds."
    },
    {
      label: "watch",
      min: 0.1,
      tone: "accent" as Health["tone"],
      description: "Mild shift — keep monitoring; not necessarily actionable."
    },
    {
      label: "none",
      min: 0,
      tone: "ok",
      description: "Within the normal envelope."
    }
  ];

  function bandFor(ks: number): Band {
    for (const b of SEVERITY_BANDS) if (ks >= b.min) return b;
    return SEVERITY_BANDS[SEVERITY_BANDS.length - 1];
  }

  function severityClass(sev: string | undefined): string {
    return sev === "alert"
      ? "pill pill-danger"
      : sev === "warn"
        ? "pill pill-warn"
        : sev === "watch"
          ? "pill pill-accent"
          : sev === "none"
            ? "pill pill-ok"
            : "pill pill-muted";
  }

  function severityToneClass(sev: string | undefined): string {
    return sev === "alert"
      ? "text-danger"
      : sev === "warn"
        ? "text-warn"
        : sev === "watch"
          ? "text-accent"
          : "text-ok";
  }

  // ─── Per-feature explainers ─────────────────────────────────────────
  function featureUnit(name: string): "MYR" | "tx" | "" {
    if (name.startsWith("amount_") || name.startsWith("discount_") || name.endsWith("_myr"))
      return "MYR";
    if (name.startsWith("txn_count")) return "tx";
    return "";
  }

  function featureWindow(name: string): string {
    if (name.endsWith("_5m")) return "5-minute window";
    if (name.endsWith("_24h")) return "24-hour window";
    if (name.endsWith("_total")) return "lifetime counter";
    return "rolling feature";
  }

  function featureMeaning(name: string): string {
    switch (name) {
      case "txn_count_5m":
        return "Transactions per customer in the last 5 minutes — drives velocity rules.";
      case "txn_count_24h":
        return "Transactions per customer in the last 24 hours — drives churn + iForest features.";
      case "txn_count_total":
        return "All-time transaction count per customer.";
      case "txn_velocity_5m":
        return "Smoothed tx/min over the last 5 minutes.";
      case "amount_sum_5m":
        return "MYR spent per customer in the last 5 minutes — high-value rule input.";
      case "discount_sum_5m":
        return "MYR of promo discounts applied in the last 5 minutes — promo-abuse rule input.";
      case "spend_24h_myr":
        return "MYR spent per customer in the last 24 hours — drives LTV + churn models.";
      default:
        return `Feature window: ${featureWindow(name)}.`;
    }
  }

  function fmtFeatureValue(name: string, v: number): string {
    const u = featureUnit(name);
    if (u === "MYR") return fmtMyr(v);
    if (u === "tx") return Number.isInteger(v) ? v.toString() : v.toFixed(2);
    return v.toFixed(3);
  }

  /** Plain-English summary of how the *current* window differs from baseline. */
  function explainShift(item: FeatureDriftItem): string {
    const cm = item.current?.mean ?? 0;
    const bm = item.baseline?.mean ?? 0;
    const cn = item.current?.n ?? 0;
    const bn = item.baseline?.n ?? 0;
    const f = (n: number) => fmtFeatureValue(item.feature_name, n);
    if (bm === 0 && cm === 0)
      return `Mean is 0 in both windows; KS picked up tail/quantile differences (current n=${cn.toLocaleString()}, baseline n=${bn.toLocaleString()}).`;
    if (bm === 0)
      return `Current mean ${f(cm)} vs baseline 0 — feature became active (current n=${cn.toLocaleString()}, baseline n=${bn.toLocaleString()}).`;
    const ratio = cm / bm;
    const pct = ((cm - bm) / bm) * 100;
    const dir = cm > bm ? "higher" : "lower";
    let mag: string;
    if (Math.abs(pct) < 5) mag = "essentially flat in mean";
    else if (Math.abs(pct) < 20) mag = `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}% ${dir}`;
    else
      mag = `${ratio >= 1 ? ratio.toFixed(2) : (1 / ratio).toFixed(2)}× ${dir} (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%)`;
    return `Current mean ${f(cm)} vs baseline ${f(bm)} — ${mag}. Sample sizes: current n=${cn.toLocaleString()}, baseline n=${bn.toLocaleString()}.`;
  }

  function explainPValue(p: number): string {
    if (p < 0.001) return "p < 0.001 — very unlikely to be noise.";
    if (p < 0.01) return `p ≈ ${p.toFixed(3)} — strong signal.`;
    if (p < 0.05) return `p ≈ ${p.toFixed(3)} — likely real shift.`;
    return `p ≈ ${p.toFixed(3)} — could be noise.`;
  }

  // ─── Lookup ─────────────────────────────────────────────────────────
  let lookupId = $state("cust_000001");
  let lookupResult = $state<FeatureVector | null>(null);
  let lookupError = $state<string | null>(null);
  let lookupLoading = $state(false);
  let showRawLookup = $state(false);

  async function lookup() {
    lookupError = null;
    lookupResult = null;
    lookupLoading = true;
    try {
      lookupResult = (await featuresApi.get(lookupId.trim())) as FeatureVector;
    } catch (e) {
      lookupError = e instanceof Error ? e.message : "lookup failed";
    } finally {
      lookupLoading = false;
    }
  }

  /** Stable display order of known feature fields. */
  const LOOKUP_FIELDS: { key: keyof FeatureVector; label: string }[] = [
    { key: "txn_count_5m", label: "txn_count_5m" },
    { key: "amount_sum_5m", label: "amount_sum_5m" },
    { key: "discount_sum_5m", label: "discount_sum_5m" },
    { key: "txn_count_total", label: "txn_count_total" }
  ];

  function lookupAge(iso: string | undefined): { label: string; tone: Health["tone"] } {
    if (!iso) return { label: "—", tone: "muted" };
    const ts = Date.parse(iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z");
    if (Number.isNaN(ts)) return { label: iso, tone: "muted" };
    const sec = Math.max(0, (Date.now() - ts) / 1000);
    const tone: Health["tone"] = sec <= 60 ? "ok" : sec <= 300 ? "warn" : "danger";
    return { label: humanizeSeconds(sec) + " ago", tone };
  }

  onMount(() => {
    loadFreshness();
    loadDrift();
  });
</script>

<div class="space-y-6">
  <header class="flex items-start justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold">Feature store</h1>
      <p class="text-sm text-muted">
        Online features written by the change-stream <code class="font-mono text-xs">feature_engineer</code>
        + Spark batch. The drift detector ticks every 15 minutes and compares the latest window
        against a 30-day baseline using the two-sample Kolmogorov–Smirnov test.
      </p>
    </div>
    <button
      class="btn"
      onclick={() => {
        loadFreshness();
        loadDrift();
      }}
      disabled={driftLoading}
    >
      {driftLoading ? "Refreshing…" : "Refresh"}
    </button>
  </header>

  <!-- ── Pipeline freshness ────────────────────────────────────────── -->
  <Section
    title="Pipeline freshness"
    subtitle="How long ago each sampled feature row was last written"
  >
    {#if snapshotErr}
      <p class="text-sm text-danger">{snapshotErr}</p>
    {:else}
      <div class="grid-cards">
        <KpiTile
          label="Sampled rows"
          value={snapshot?.sampled ?? "—"}
          sub="Most recent feature documents inspected"
        />
        <KpiTile
          label="Median lag"
          value={humanizeSeconds(snapshot?.median_lag_seconds)}
          sub={`Half of rows are fresher than this. SLO ≤ ${snapshot?.fresh_threshold_seconds ?? 60}s.`}
          accent={pipelineHealth.tone === "ok" ? "ok" : pipelineHealth.tone === "warn" ? "warn" : "danger"}
        />
        <KpiTile
          label="p95 lag"
          value={humanizeSeconds(snapshot?.p95_lag_seconds)}
          sub="95% of rows are fresher than this — long tail of the pipeline."
          accent={(snapshot?.p95_lag_seconds ?? 0) > 120 ? "warn" : "default"}
        />
        <KpiTile
          label="Fresh ≤ 60s"
          value={snapshot?.fresh_share != null
            ? Math.round(snapshot.fresh_share * 100) + "%"
            : "—"}
          sub="Share of sampled rows meeting the freshness SLO"
          accent={(snapshot?.fresh_share ?? 0) >= 0.95
            ? "ok"
            : (snapshot?.fresh_share ?? 0) >= 0.5
              ? "warn"
              : "danger"}
        />
      </div>

      <div class="mt-4 flex items-start gap-2 rounded-md border p-3 text-sm {toneClass(pipelineHealth.tone)}">
        <Activity class="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <div class="font-medium">Pipeline status: {pipelineHealth.label}</div>
          <p class="mt-0.5 text-xs opacity-90">{pipelineHealth.message}</p>
        </div>
      </div>
    {/if}
  </Section>

  <!-- ── Drift snapshot (enriched) ─────────────────────────────────── -->
  <Section
    title="Drift snapshot"
    subtitle="Top features ranked by KS divergence — click a row to see what changed and who's affected"
  >
    {#snippet actions()}
      <button class="btn" onclick={loadDrift} disabled={driftLoading}>
        {driftLoading ? "Refreshing…" : "Refresh"}
      </button>
    {/snippet}

    {#if driftLoading}
      <div class="space-y-2" data-testid="drift-snapshot-loading">
        {#each Array(4) as _}
          <div class="h-12 w-full animate-pulse rounded bg-bg/60"></div>
        {/each}
      </div>
    {:else if driftErr}
      <p class="text-sm text-danger">{driftErr}</p>
    {:else if driftItems.length === 0}
      <p class="text-sm text-muted">No drift measurements yet — the detector hasn't ticked.</p>
    {:else}
      <div class="overflow-hidden rounded-md border border-border" data-testid="drift-snapshot">
        <table class="w-full text-sm">
          <thead class="bg-bg/60 text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th class="w-6 px-3 py-2"></th>
              <th class="px-3 py-2">Feature</th>
              <th class="px-3 py-2">Severity</th>
              <th class="px-3 py-2">KS · band</th>
              <th class="px-3 py-2">What changed</th>
              <th class="px-3 py-2 text-right">Last seen</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            {#each driftItems as item}
              {@const band = bandFor(item.ks_statistic)}
              {@const isOpen = !!expanded[item.feature_name]}
              {@const impact = impactCache[item.feature_name]}
              <tr
                class="cursor-pointer align-top hover:bg-bg/40"
                onclick={() => toggleExpand(item.feature_name)}
              >
                <td class="px-3 py-2 text-muted">
                  {#if isOpen}
                    <ChevronDown class="h-4 w-4" />
                  {:else}
                    <ChevronRight class="h-4 w-4" />
                  {/if}
                </td>
                <td class="px-3 py-2">
                  <div class="font-mono text-xs">
                    <a
                      class="hover:underline"
                      href="/features/{encodeURIComponent(item.feature_name)}"
                      onclick={(e) => e.stopPropagation()}
                    >
                      {item.feature_name}
                    </a>
                  </div>
                  <div class="mt-0.5 text-[11px] text-muted">{featureWindow(item.feature_name)}</div>
                </td>
                <td class="px-3 py-2">
                  <span class={severityClass(item.severity)}>
                    <span class="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
                    {item.severity}
                  </span>
                </td>
                <td class="px-3 py-2">
                  <div class="tabular-nums {severityToneClass(item.severity)}">
                    {item.ks_statistic.toFixed(3)}
                  </div>
                  <div class="text-[11px] text-muted">
                    {band.label === "none"
                      ? `< ${SEVERITY_BANDS[2].min.toFixed(2)} (none)`
                      : `≥ ${band.min.toFixed(2)} (${band.label})`}
                  </div>
                </td>
                <td class="px-3 py-2 text-xs text-muted">
                  {explainShift(item)}
                </td>
                <td class="px-3 py-2 text-right text-xs text-muted">
                  {fmtRelative(item.measured_at)}
                </td>
              </tr>

              {#if isOpen}
                <tr class="bg-bg/20">
                  <td></td>
                  <td colspan="5" class="px-3 py-3">
                    <div class="grid gap-4 lg:grid-cols-3">
                      <!-- What this feature is -->
                      <div>
                        <div class="mb-1 text-[11px] uppercase tracking-wide text-muted">
                          What this feature is
                        </div>
                        <p class="text-sm">{featureMeaning(item.feature_name)}</p>
                        <p class="mt-2 text-xs text-muted">
                          {explainPValue(item.p_value)}
                          {band.description}
                        </p>
                      </div>

                      <!-- Stats grid -->
                      <div>
                        <div class="mb-1 text-[11px] uppercase tracking-wide text-muted">
                          Distribution stats (current vs baseline)
                        </div>
                        <table class="w-full border-separate border-spacing-y-0.5 text-xs">
                          <thead class="text-muted">
                            <tr>
                              <th class="text-left font-normal"></th>
                              <th class="text-right font-normal">Current</th>
                              <th class="text-right font-normal">Baseline</th>
                            </tr>
                          </thead>
                          <tbody class="tabular-nums">
                            <tr>
                              <td class="text-muted">n</td>
                              <td class="text-right">{(item.current?.n ?? 0).toLocaleString()}</td>
                              <td class="text-right">{(item.baseline?.n ?? 0).toLocaleString()}</td>
                            </tr>
                            <tr>
                              <td class="text-muted">mean</td>
                              <td class="text-right">{fmtFeatureValue(item.feature_name, item.current?.mean ?? 0)}</td>
                              <td class="text-right">{fmtFeatureValue(item.feature_name, item.baseline?.mean ?? 0)}</td>
                            </tr>
                            <tr>
                              <td class="text-muted">std</td>
                              <td class="text-right">{fmtFeatureValue(item.feature_name, item.current?.std ?? 0)}</td>
                              <td class="text-right">{fmtFeatureValue(item.feature_name, item.baseline?.std ?? 0)}</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>

                      <!-- Impact / consumers -->
                      <div>
                        <div class="mb-1 text-[11px] uppercase tracking-wide text-muted">
                          Downstream impact
                        </div>
                        {#if impact === "loading" || impact === undefined}
                          <div class="space-y-1">
                            <div class="h-3 w-2/3 animate-pulse rounded bg-bg/60"></div>
                            <div class="h-3 w-1/2 animate-pulse rounded bg-bg/60"></div>
                          </div>
                        {:else if impact === "error"}
                          <p class="flex items-center gap-1 text-xs text-danger">
                            <AlertTriangle class="h-3.5 w-3.5" />
                            Couldn't load impact analysis.
                          </p>
                        {:else}
                          <div class="flex flex-wrap items-center gap-2 text-xs">
                            <span class="pill pill-warn">
                              {impact.blast_radius?.rule_count ?? 0} rules
                            </span>
                            <span class="pill pill-danger">
                              {impact.blast_radius?.model_count ?? 0} models
                            </span>
                            <span class="pill pill-accent">
                              {impact.recommended_action ?? "monitor"}
                            </span>
                          </div>
                          {#if impact.affected_consumers && impact.affected_consumers.length > 0}
                            <ul class="mt-2 space-y-0.5 text-xs">
                              {#each impact.affected_consumers as c}
                                <li class="flex items-center gap-2">
                                  <span class="font-mono">{c.name}</span>
                                  <span class="text-[10px] uppercase tracking-wide text-muted">
                                    {c.type}
                                  </span>
                                </li>
                              {/each}
                            </ul>
                          {:else}
                            <p class="mt-2 text-xs text-muted">No downstream consumers known.</p>
                          {/if}
                          <a
                            class="mt-3 inline-block text-xs text-accent hover:underline"
                            href="/features/{encodeURIComponent(item.feature_name)}"
                            onclick={(e) => e.stopPropagation()}
                          >
                            Investigate this feature →
                          </a>
                        {/if}
                      </div>
                    </div>
                  </td>
                </tr>
              {/if}
            {/each}
          </tbody>
        </table>
      </div>

      <!-- Severity bands legend -->
      <div class="mt-3 rounded-md border border-border bg-bg/40 p-3 text-xs">
        <div class="mb-2 flex items-center gap-2 text-muted">
          <Info class="h-3.5 w-3.5" />
          <span class="uppercase tracking-wide">How to read severity</span>
        </div>
        <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {#each SEVERITY_BANDS as b}
            <div class="flex items-start gap-2">
              <span class="{severityClass(b.label)} mt-0.5 shrink-0">
                <span class="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
                {b.label}
              </span>
              <div>
                <div class="text-fg">
                  {b.label === "none" ? `KS < ${SEVERITY_BANDS[2].min.toFixed(2)}` : `KS ≥ ${b.min.toFixed(2)}`}
                </div>
                <div class="text-muted">{b.description}</div>
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </Section>

  <!-- ── Lookup ────────────────────────────────────────────────────── -->
  <Section
    title="Lookup"
    subtitle="Inspect a specific customer's online feature vector"
  >
    {#snippet actions()}
      <button class="btn btn-primary" onclick={lookup} disabled={lookupLoading}>
        <Search class="h-3.5 w-3.5" />
        {lookupLoading ? "Looking up…" : "Lookup"}
      </button>
    {/snippet}

    <input
      class="input"
      bind:value={lookupId}
      placeholder="cust_000001"
      onkeydown={(e) => {
        if (e.key === "Enter") lookup();
      }}
    />

    {#if lookupError}
      <p class="mt-3 flex items-center gap-2 text-sm text-danger">
        <AlertTriangle class="h-4 w-4" />
        {lookupError}
      </p>
    {/if}

    {#if lookupResult}
      {@const fresh = lookupAge(lookupResult.updated_at)}
      <div class="mt-4 rounded-md border border-border">
        <div class="flex items-center justify-between gap-3 border-b border-border bg-bg/40 px-3 py-2">
          <div>
            <div class="font-mono text-xs">{lookupResult.customer_id}</div>
            <div class="text-[11px] text-muted">
              Last updated {fresh.label}
              {#if lookupResult.last_txn_at}
                · last txn {fmtRelative(lookupResult.last_txn_at)}
              {/if}
            </div>
          </div>
          <span class={`pill ${fresh.tone === "ok" ? "pill-ok" : fresh.tone === "warn" ? "pill-warn" : fresh.tone === "danger" ? "pill-danger" : "pill-muted"}`}>
            <span class="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
            {fresh.tone === "ok" ? "fresh" : fresh.tone === "warn" ? "stale" : fresh.tone === "danger" ? "very stale" : "unknown"}
          </span>
        </div>
        <table class="w-full text-sm">
          <tbody class="divide-y divide-border">
            {#each LOOKUP_FIELDS as f}
              {@const raw = lookupResult[f.key]}
              {@const has = raw != null}
              <tr>
                <td class="w-1/3 px-3 py-2">
                  <div class="font-mono text-xs">{f.label}</div>
                  <div class="text-[11px] text-muted">{featureMeaning(f.label)}</div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">
                  {#if has && typeof raw === "number"}
                    <span>{fmtFeatureValue(f.label, raw)}</span>
                    {#if featureUnit(f.label) && featureUnit(f.label) !== "MYR"}
                      <span class="ml-1 text-xs text-muted">{featureUnit(f.label)}</span>
                    {/if}
                  {:else}
                    <span class="text-muted">—</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <button
        class="btn mt-3"
        onclick={() => (showRawLookup = !showRawLookup)}
      >
        <Code2 class="h-3.5 w-3.5" />
        {showRawLookup ? "Hide raw document" : "Show raw document"}
      </button>
      {#if showRawLookup}
        <pre class="mt-2 max-h-96 overflow-auto rounded-md bg-bg/60 p-3 text-xs">{JSON.stringify(
            lookupResult,
            null,
            2
          )}</pre>
      {/if}
    {/if}
  </Section>
</div>
