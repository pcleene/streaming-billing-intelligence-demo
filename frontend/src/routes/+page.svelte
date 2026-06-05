<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import FeatureDriftTile from "$lib/components/FeatureDriftTile.svelte";
  import PerEntityHealthRow from "$lib/components/PerEntityHealthRow.svelte";
  import LatestFeaturesCard from "$lib/components/LatestFeaturesCard.svelte";
  import GeographicHeatmap from "$lib/components/GeographicHeatmap.svelte";
  import ResolutionVelocityTile from "$lib/components/ResolutionVelocityTile.svelte";
  import TopDriftFeatures from "$lib/components/TopDriftFeatures.svelte";
  import WhatChangedTodayDigest from "$lib/components/WhatChangedTodayDigest.svelte";
  import TransactionRateTile from "$lib/components/TransactionRateTile.svelte";
  import { quarantineApi, featuresApi, dashboardApi } from "$lib/api";
  import type { EntityHealthSummary, LatestFeaturesSummary } from "$lib/api";
  import { SseClient } from "$lib/sse";
  import type { LiveTxn, MetricsTick, QuarantineCase } from "$lib/types";
  import { fmtRelative, fmtMyr } from "$lib/utils";

  let metrics = $state<MetricsTick>({
    transactions_per_sec: 0,
    quarantine_per_sec: 0,
    p99_eval_ms: 0,
    p50_eval_ms: 0,
    ts: 0
  });
  let recent = $state<QuarantineCase[]>([]);
  let liveTxns = $state<LiveTxn[]>([]);
  const LIVE_TXN_CAP = 20;

  // Both the in-process consumer and the API-side change-stream tail can
  // emit `new_txn` for the same write. Drop duplicates by transaction_id
  // so the table doesn't show the same row twice.
  function pushLiveTxn(t: LiveTxn) {
    const id = t.transaction_id ?? null;
    const next = id ? liveTxns.filter((x) => x.transaction_id !== id) : liveTxns.slice();
    next.unshift(t);
    liveTxns = next.slice(0, LIVE_TXN_CAP);
  }

  function fmtTxnTime(t: LiveTxn): string {
    const iso = (t.timestamp ?? t.created_at) as string | null | undefined;
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], { hour12: false });
  }
  let freshness = $state<{ median_lag_seconds: number | null; p95_lag_seconds: number | null; fresh_share?: number } | null>(null);
  let entityHealth = $state<EntityHealthSummary | null>(null);
  let latestFeatures = $state<LatestFeaturesSummary | null>(null);
  let loadingHealth = $state(true);
  let loadingFeatures = $state(true);

  const sse = new SseClient();

  async function loadCases() {
    try {
      const r = await quarantineApi.list({ status: "open", limit: 12 });
      recent = r.items;
    } catch (_e) {
      /* swallow during demo */
    }
  }
  async function loadFreshness() {
    try {
      freshness = await featuresApi.freshness();
    } catch (_e) {
      /* swallow during demo */
    }
  }
  async function loadHealth() {
    loadingHealth = true;
    try {
      entityHealth = await dashboardApi.entityHealth();
    } catch (_e) {
      entityHealth = null;
    } finally {
      loadingHealth = false;
    }
  }

  async function loadLatestFeatures() {
    loadingFeatures = true;
    try {
      latestFeatures = await dashboardApi.latestFeatures();
    } catch (_e) {
      latestFeatures = null;
    } finally {
      loadingFeatures = false;
    }
  }

  onMount(() => {
    loadCases();
    loadFreshness();
    loadHealth();
    loadLatestFeatures();
    sse.start();
    sse.on<MetricsTick>("metric_tick", (m) => (metrics = m));
    sse.on<QuarantineCase>("new_case", (c) => {
      recent = [c, ...recent].slice(0, 12);
    });
    sse.on<LiveTxn>("new_txn", (t) => pushLiveTxn(t));
  });
  onDestroy(() => sse.stop());
</script>

<div class="space-y-6">
  <header class="flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold">Operations Dashboard</h1>
      <p class="text-sm text-muted">Live ingest, evaluation latency, and the open queue.</p>
    </div>
    <div class="text-xs text-muted">
      tick: {metrics.ts ? fmtRelative(new Date(metrics.ts * 1000).toISOString()) : "—"}
    </div>
  </header>

  <div class="grid-cards">
    <KpiTile label="Transactions / sec" value={metrics.transactions_per_sec} accent="accent" />
    <KpiTile label="Quarantines / sec" value={metrics.quarantine_per_sec} accent="warn" />
    <KpiTile label="Eval p50" value={metrics.p50_eval_ms} unit="ms" accent="ok" />
    <KpiTile label="Eval p99" value={metrics.p99_eval_ms} unit="ms" accent={metrics.p99_eval_ms > 200 ? "danger" : "ok"} sub="target ≤ 200 ms" />
  </div>

  <!-- Live transaction throughput + drift summary -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <TransactionRateTile minutes={60} />
    <FeatureDriftTile />
  </div>

  <!-- Per-entity health row (cross-entity revenue tile retired - relied on
       ASP-side `entity` enrichment that isn't deployed). -->
  <PerEntityHealthRow data={entityHealth} loading={loadingHealth} />

  <!-- Polish tiles -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <GeographicHeatmap />
    <TopDriftFeatures />
  </div>
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
    <ResolutionVelocityTile />
    <div class="lg:col-span-2"><WhatChangedTodayDigest /></div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
    <div class="lg:col-span-2 space-y-4">
      <Section title="Open quarantine queue" subtitle="Most recent open cases (live)">
        {#if recent.length === 0}
          <div class="text-sm text-muted">No open cases. Run the simulator to populate.</div>
        {:else}
          <ul class="divide-y divide-border">
            {#each recent as c}
              <li class="flex items-center gap-4 py-3 text-sm">
                <SeverityBadge severity={c.severity} />
                <a class="font-mono text-accent hover:underline" href={`/quarantine/${c.case_id}`}>
                  {c.case_id}
                </a>
                <span class="text-muted">{c.customer_id}</span>
                <span class="ml-auto text-xs text-muted">{fmtRelative(c.created_at)}</span>
                <span class="text-xs text-fg/80 w-20 text-right">{fmtMyr(c.amount)}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </Section>

      <Section title="Live transactions" subtitle="Most recent accepted Kafka events">
        {#if liveTxns.length === 0}
          <div class="text-sm text-muted">No transactions yet. Start the MSK consumer + simulator to populate.</div>
        {:else}
          <div class="overflow-hidden rounded-lg border border-border">
            <table class="w-full text-left text-sm">
              <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th class="px-3 py-2">Time</th>
                  <th class="px-3 py-2">Customer</th>
                  <th class="px-3 py-2 text-right">Amount</th>
                  <th class="px-3 py-2">Charge</th>
                  <th class="px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {#each liveTxns as t (t.transaction_id ?? `${t.customer_id}-${t.timestamp}`)}
                  <tr class="border-t border-border">
                    <td class="px-3 py-2 text-xs text-muted tabular-nums">{fmtTxnTime(t)}</td>
                    <td class="px-3 py-2 font-mono text-xs text-fg/80">{(t.customer_id ?? "—").slice(0, 18)}</td>
                    <td class="px-3 py-2 text-right tabular-nums">{fmtMyr(t.amount ?? undefined)}</td>
                    <td class="px-3 py-2 text-xs">{t.charge_code ?? "—"}</td>
                    <td class="px-3 py-2 text-xs"><span class="pill pill-muted">{t.status ?? "—"}</span></td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Section>
    </div>

    <div class="space-y-4">
      <Section title="Feature freshness" subtitle="Online feature lag">
        <div class="space-y-3 text-sm">
          <div class="flex justify-between">
            <span class="text-muted">Median lag</span>
            <span>{freshness?.median_lag_seconds ?? "—"} s</span>
          </div>
          <div class="flex justify-between">
            <span class="text-muted">p95 lag</span>
            <span>{freshness?.p95_lag_seconds ?? "—"} s</span>
          </div>
          <div class="flex justify-between">
            <span class="text-muted">Fresh share (≤ 60 s)</span>
            <span>{freshness?.fresh_share != null ? Math.round(freshness.fresh_share * 100) + "%" : "—"}</span>
          </div>
        </div>
      </Section>
      <LatestFeaturesCard data={latestFeatures} loading={loadingFeatures} />
    </div>
  </div>
</div>
