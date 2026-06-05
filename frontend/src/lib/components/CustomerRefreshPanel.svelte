<script lang="ts">
  import Section from "./Section.svelte";
  import { RefreshCw } from "lucide-svelte";
  import { customersRefreshApi } from "$lib/api";
  import type { RefreshResponse, MetricsRefreshResponse } from "$lib/types";
  import { fmtDate, fmtRelative } from "$lib/utils";

  interface Props {
    customerId: string;
  }
  let { customerId }: Props = $props();

  let force = $state(false);
  let busy360 = $state(false);
  let busyMetrics = $state(false);
  let last360 = $state<RefreshResponse | null>(null);
  let lastMetrics = $state<MetricsRefreshResponse | null>(null);
  let err360 = $state<string | null>(null);
  let errMetrics = $state<string | null>(null);
  let actedAt360 = $state<string | null>(null);
  let actedAtMetrics = $state<string | null>(null);

  async function refresh360() {
    if (!customerId) return;
    busy360 = true;
    err360 = null;
    try {
      last360 = await customersRefreshApi.refresh360(customerId, force);
      actedAt360 = new Date().toISOString();
    } catch (e) {
      err360 = e instanceof Error ? e.message : String(e);
    } finally {
      busy360 = false;
    }
  }

  async function refreshMetrics() {
    if (!customerId) return;
    busyMetrics = true;
    errMetrics = null;
    try {
      lastMetrics = await customersRefreshApi.metricsRefresh(customerId, force);
      actedAtMetrics = new Date().toISOString();
    } catch (e) {
      errMetrics = e instanceof Error ? e.message : String(e);
    } finally {
      busyMetrics = false;
    }
  }

  const status360 = $derived.by(() => {
    if (err360) return { tone: "text-danger", text: err360 };
    if (!last360) return null;
    const refreshed = (last360 as { refreshed?: boolean }).refreshed;
    const skipped = (last360 as { skipped_reason?: string | null }).skipped_reason;
    if (refreshed === false && skipped) {
      return { tone: "text-warn", text: `skipped: ${skipped}` };
    }
    return { tone: "text-ok", text: "refreshed" };
  });

  const statusMetrics = $derived.by(() => {
    if (errMetrics) return { tone: "text-danger", text: errMetrics };
    if (!lastMetrics) return null;
    if (!lastMetrics.computed) {
      return {
        tone: "text-warn",
        text: `skipped${lastMetrics.skipped_reason ? `: ${lastMetrics.skipped_reason}` : ""}`
      };
    }
    return { tone: "text-ok", text: "computed" };
  });
</script>

<Section title="Refresh & metrics" subtitle="On-demand recompute of the 360 view + cross-entity metrics">
  <div class="flex flex-wrap items-center gap-3">
    <button
      type="button"
      data-testid="btn-refresh-360"
      class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-elevated disabled:opacity-50"
      onclick={refresh360}
      disabled={busy360}
    >
      <RefreshCw class={"h-3.5 w-3.5 " + (busy360 ? "animate-spin" : "")} aria-hidden="true" />
      Refresh 360
    </button>
    <button
      type="button"
      data-testid="btn-refresh-metrics"
      class="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-elevated disabled:opacity-50"
      onclick={refreshMetrics}
      disabled={busyMetrics}
    >
      <RefreshCw class={"h-3.5 w-3.5 " + (busyMetrics ? "animate-spin" : "")} aria-hidden="true" />
      Refresh metrics
    </button>
    <label class="ml-auto flex items-center gap-1.5 text-xs text-muted">
      <input type="checkbox" bind:checked={force} />
      <span>force</span>
    </label>
  </div>

  <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
    <div class="rounded-md border border-border p-3" data-testid="result-360">
      <div class="flex items-center justify-between">
        <span class="font-medium">360 view</span>
        {#if status360}<span class={status360.tone}>{status360.text}</span>{/if}
      </div>
      {#if last360}
        <div class="mt-1 text-muted">
          {#if last360.computed_at}
            <div>computed: {fmtDate(last360.computed_at)} ({fmtRelative(last360.computed_at)})</div>
          {/if}
          {#if last360.source}<div>source: <span class="font-mono">{last360.source}</span></div>{/if}
          {#if actedAt360}<div>action: {fmtRelative(actedAt360)}</div>{/if}
        </div>
      {:else if !err360}
        <p class="mt-1 text-muted">No action yet.</p>
      {/if}
    </div>

    <div class="rounded-md border border-border p-3" data-testid="result-metrics">
      <div class="flex items-center justify-between">
        <span class="font-medium">Cross-entity metrics</span>
        {#if statusMetrics}<span class={statusMetrics.tone}>{statusMetrics.text}</span>{/if}
      </div>
      {#if lastMetrics}
        <div class="mt-1 text-muted">
          {#if lastMetrics.cross_entity_metrics}
            <div>
              keys:
              <span class="font-mono">
                {Object.keys(lastMetrics.cross_entity_metrics).slice(0, 4).join(", ") || "—"}
              </span>
            </div>
          {/if}
          {#if actedAtMetrics}<div>action: {fmtRelative(actedAtMetrics)}</div>{/if}
        </div>
      {:else if !errMetrics}
        <p class="mt-1 text-muted">No action yet.</p>
      {/if}
    </div>
  </div>
</Section>
