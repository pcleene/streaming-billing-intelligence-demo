<script lang="ts">
  import KpiTile from "$lib/components/KpiTile.svelte";
  import type { BurstSummaryStats } from "$lib/types";

  interface Props {
    summary?: BurstSummaryStats;
    /** Whether the run is still in progress — drives the duration tile. */
    active?: boolean;
  }
  let { summary = {}, active = false }: Props = $props();

  // Prefer PR-11 keys; fall back to legacy aliases on older payloads.
  const peakTps = $derived(summary.peak_observed_tps ?? summary.peak_tps ?? 0);
  const peakP99 = $derived(summary.peak_p99_ms ?? summary.p99_rule_eval_ms_max ?? 0);
  const sampleCount = $derived(summary.sample_count ?? summary.row_count ?? 0);
  const breaches = $derived(summary.rule_eval_p99_threshold_breaches ?? 0);
  const duration = $derived(summary.duration_seconds ?? 0);

  function fmtDuration(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0";
    if (seconds < 60) return seconds.toFixed(1);
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds - m * 60);
    return `${m}m ${s}s`;
  }
</script>

<div class="grid-cards">
  <KpiTile
    label="Peak observed TPS"
    value={Number(peakTps).toFixed(0)}
    accent="accent"
    sub={active ? "in-flight" : "completed"}
  />
  <KpiTile
    label="Peak p99"
    value={Number(peakP99).toFixed(0)}
    unit="ms"
    accent={Number(peakP99) > 200 ? "danger" : "ok"}
    sub="target ≤ 200 ms"
  />
  <KpiTile
    label="Samples"
    value={Number(sampleCount).toFixed(0)}
    accent="default"
  />
  <KpiTile
    label="p99 breaches"
    value={Number(breaches).toFixed(0)}
    accent={Number(breaches) > 0 ? "warn" : "ok"}
    sub="rule_eval > 200 ms"
  />
  <KpiTile
    label="Duration"
    value={fmtDuration(Number(duration))}
    unit={Number(duration) < 60 ? "s" : ""}
    accent="default"
  />
</div>
