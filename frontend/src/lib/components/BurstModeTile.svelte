<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { systemMetricsApi, type SystemMetricSample } from "$lib/api";

  interface Props {
    refreshSeconds?: number;
    windowMinutes?: number;
  }
  let { refreshSeconds = 30, windowMinutes = 60 }: Props = $props();

  let samples: SystemMetricSample[] = $state([]);
  let mode = $state<"steady" | "burst" | "idle">("idle");
  let burstRuns: string[] = $state([]);
  let loading = $state(true);
  let err: string | null = $state(null);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      const r = await systemMetricsApi.recent(windowMinutes);
      samples = r.samples;
      mode = r.current_mode;
      burstRuns = r.burst_run_ids;
      err = null;
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    refresh();
    timer = setInterval(refresh, refreshSeconds * 1000);
  });

  onDestroy(() => {
    if (timer) clearInterval(timer);
  });

  // Sparkline geometry — TPS in green, p99 in amber, single-pass scaled.
  const W = 320;
  const H = 90;
  const PAD = 6;

  const tpsPath = $derived(buildPath(samples.map((s) => s.observed_tps)));
  const p99Path = $derived(buildPath(samples.map((s) => s.p99_ms_ingest)));

  function buildPath(values: number[]): string {
    if (values.length === 0) return "";
    const max = Math.max(1, ...values);
    const stepX = (W - 2 * PAD) / Math.max(1, values.length - 1);
    return values
      .map((v, i) => {
        const x = PAD + i * stepX;
        const y = H - PAD - (v / max) * (H - 2 * PAD);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  const latest = $derived(samples.length ? samples[samples.length - 1] : null);
  const accent = $derived(
    mode === "burst" ? "text-warn" : mode === "steady" ? "text-ok" : "text-muted"
  );
</script>

<div class="card p-4">
  <div class="flex items-center justify-between">
    <div class="text-xs uppercase tracking-wide text-muted">Burst mode</div>
    <div class="text-xs {accent}">{mode}</div>
  </div>

  {#if loading}
    <div class="mt-3 text-sm text-muted">Loading…</div>
  {:else if err}
    <div class="mt-3 text-sm text-danger">{err}</div>
  {:else if samples.length === 0}
    <div class="mt-3 text-sm text-muted">No samples yet — start the metrics_recorder worker.</div>
  {:else}
    <div class="mt-2 flex items-baseline gap-4">
      <div>
        <div class="text-2xl font-semibold {accent}">
          {latest?.observed_tps.toFixed(0) ?? 0}
        </div>
        <div class="text-xs text-muted">tps observed</div>
      </div>
      <div>
        <div class="text-2xl font-semibold">
          {latest?.p99_ms_ingest.toFixed(0) ?? 0}
        </div>
        <div class="text-xs text-muted">ms p99 ingest</div>
      </div>
    </div>

    <svg viewBox="0 0 {W} {H}" class="mt-2 w-full" preserveAspectRatio="none">
      {#if tpsPath}
        <path d={tpsPath} fill="none" stroke="currentColor" class="text-ok" stroke-width="1.5" />
      {/if}
      {#if p99Path}
        <path d={p99Path} fill="none" stroke="currentColor" class="text-warn" stroke-width="1.5" />
      {/if}
    </svg>

    <div class="mt-2 flex justify-between text-xs text-muted">
      <span><span class="text-ok">●</span> tps</span>
      <span><span class="text-warn">●</span> p99 ms</span>
      <span>{windowMinutes}m window</span>
    </div>

    {#if burstRuns.length > 0}
      <div class="mt-2 text-xs text-muted">
        burst runs: {burstRuns.length} · latest <code>{burstRuns[burstRuns.length - 1]}</code>
      </div>
    {/if}
  {/if}

  <!-- H4: link to burst metrics detail page (PR-13) -->
  <div class="mt-3 text-right text-xs">
    <a class="text-accent hover:underline" href="/metrics">View details →</a>
  </div>
</div>
