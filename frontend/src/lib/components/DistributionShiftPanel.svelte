<script lang="ts">
  import type { DriftStatsBlock } from "$lib/types";
  import { fmtMyr } from "$lib/utils";
  import { BarChart3, AlertTriangle } from "lucide-svelte";

  interface Props {
    /** Distribution stats from the most recent detector tick. */
    current: DriftStatsBlock | null | undefined;
    /** Distribution stats from the 30-day baseline window. */
    baseline: DriftStatsBlock | null | undefined;
    /** Used to choose units (MYR vs tx) when formatting values. */
    featureName: string;
    loading?: boolean;
  }
  let { current, baseline, featureName, loading = false }: Props = $props();

  type Unit = "MYR" | "tx" | "raw";

  function unitFor(name: string): Unit {
    if (name.startsWith("amount_") || name.startsWith("discount_") || name.endsWith("_myr"))
      return "MYR";
    if (name.startsWith("txn_count")) return "tx";
    return "raw";
  }

  const unit = $derived(unitFor(featureName));

  function fmt(v: number | undefined): string {
    if (v == null || Number.isNaN(v)) return "—";
    if (unit === "MYR") return fmtMyr(v);
    if (unit === "tx") return Number.isInteger(v) ? v.toString() : v.toFixed(2);
    return v.toFixed(3);
  }

  /** Stat rows in a stable display order. Keys must exist on `_stats`. */
  const STAT_ROWS: { key: keyof DriftStatsBlock; label: string }[] = [
    { key: "n", label: "samples (n)" },
    { key: "mean", label: "mean" },
    { key: "std", label: "std" },
    { key: "min", label: "min" },
    { key: "p25", label: "p25" },
    { key: "p50", label: "median (p50)" },
    { key: "p75", label: "p75" },
    { key: "p99", label: "p99" },
    { key: "max", label: "max" }
  ];

  /** Relative delta as `+X%` / `-X%`, or `—` when baseline is zero. */
  function pctDelta(c: number | undefined, b: number | undefined): string {
    if (c == null || b == null) return "—";
    if (b === 0) return c === 0 ? "0%" : "+∞";
    const pct = ((c - b) / b) * 100;
    if (!Number.isFinite(pct)) return "—";
    if (Math.abs(pct) < 0.5) return "0%";
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`;
  }

  /** Tone for the delta cell — only colour mean/p50/p99 to avoid noise. */
  function deltaTone(
    key: keyof DriftStatsBlock,
    c: number | undefined,
    b: number | undefined
  ): string {
    if (!["mean", "p50", "p99", "p75"].includes(String(key))) return "text-muted";
    if (c == null || b == null || b === 0) return "text-muted";
    const pct = Math.abs(((c - b) / b) * 100);
    if (pct < 5) return "text-muted";
    if (pct < 20) return "text-warn";
    return "text-danger";
  }

  /** Plain-English summary of the dominant shift. */
  const summary = $derived.by(() => {
    if (!current || !baseline) return "";
    const cm = current.mean ?? 0;
    const bm = baseline.mean ?? 0;
    if (bm === 0 && cm === 0)
      return "Mean is 0 in both windows — KS picked up a shape difference in the tails or quantiles.";
    if (bm === 0)
      return `The feature was 0 in baseline but now averages ${fmt(cm)} — feature has become active.`;
    const ratio = cm / bm;
    const pct = ((cm - bm) / bm) * 100;
    const dir = cm > bm ? "higher" : "lower";
    if (Math.abs(pct) < 5)
      return `Means are essentially flat (${fmt(cm)} vs ${fmt(bm)}). The KS divergence is driven by quantile/tail shifts — see p75/p99 below.`;
    const mag =
      Math.abs(pct) < 20
        ? `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`
        : `${ratio >= 1 ? ratio.toFixed(2) : (1 / ratio).toFixed(2)}× ${dir} (${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%)`;
    return `Current mean ${fmt(cm)} vs baseline ${fmt(bm)} — ${mag}. Spread (std) is ${fmt(current.std ?? 0)} now vs ${fmt(baseline.std ?? 0)} in baseline.`;
  });

  /** Compute the relative position of two means on a shared 0..max axis,
   *  for a tiny visual hint above the table. */
  const visual = $derived.by(() => {
    if (!current || !baseline) return null;
    const cMax = current.max ?? current.mean ?? 0;
    const bMax = baseline.max ?? baseline.mean ?? 0;
    const axisMax = Math.max(0.01, cMax, bMax) * 1.05;
    const baseLeft = ((baseline.mean ?? 0) / axisMax) * 100;
    const curLeft = ((current.mean ?? 0) / axisMax) * 100;
    const baseStd = ((baseline.std ?? 0) / axisMax) * 100;
    const curStd = ((current.std ?? 0) / axisMax) * 100;
    return { axisMax, baseLeft, curLeft, baseStd, curStd };
  });
</script>

<div class="card p-5">
  <header class="mb-4 flex items-center gap-2">
    <BarChart3 class="h-4 w-4 text-accent" />
    <h2 class="text-lg font-semibold">Distribution shift</h2>
    <span class="text-xs text-muted">— current window vs 30-day baseline</span>
  </header>

  {#if loading}
    <div class="space-y-2">
      <div class="h-4 w-2/3 animate-pulse rounded bg-bg/60"></div>
      <div class="h-3 w-full animate-pulse rounded bg-bg/60"></div>
      <div class="h-3 w-full animate-pulse rounded bg-bg/60"></div>
    </div>
  {:else if !current || !baseline}
    <div class="flex items-center gap-2 text-sm text-muted">
      <AlertTriangle class="h-4 w-4" />
      <span>
        No distribution stats on the latest tick — older drift docs don't carry
        <code class="font-mono text-xs">current</code> /
        <code class="font-mono text-xs">baseline</code>. Reseed or wait for the next
        detector tick.
      </span>
    </div>
  {:else}
    <p class="mb-3 text-sm text-fg">{summary}</p>

    {#if visual}
      <!-- Mean ± std visual: two horizontal bars on a shared axis -->
      <div class="mb-4 space-y-2">
        <div>
          <div class="mb-0.5 flex items-baseline justify-between text-[11px] text-muted">
            <span>Baseline mean ± std</span>
            <span class="tabular-nums text-fg">{fmt(baseline.mean)}</span>
          </div>
          <div class="relative h-2 rounded-full bg-bg/60">
            <div
              class="absolute h-full rounded-full bg-ok/30"
              style="left: {Math.max(0, visual.baseLeft - visual.baseStd)}%; width: {Math.min(100, visual.baseStd * 2)}%"
            ></div>
            <div
              class="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded bg-ok"
              style="left: {visual.baseLeft}%"
            ></div>
          </div>
        </div>
        <div>
          <div class="mb-0.5 flex items-baseline justify-between text-[11px] text-muted">
            <span>Current mean ± std</span>
            <span class="tabular-nums text-fg">{fmt(current.mean)}</span>
          </div>
          <div class="relative h-2 rounded-full bg-bg/60">
            <div
              class="absolute h-full rounded-full bg-danger/30"
              style="left: {Math.max(0, visual.curLeft - visual.curStd)}%; width: {Math.min(100, visual.curStd * 2)}%"
            ></div>
            <div
              class="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded bg-danger"
              style="left: {visual.curLeft}%"
            ></div>
          </div>
        </div>
      </div>
    {/if}

    <!-- Stat-by-stat comparison table -->
    <div class="overflow-hidden rounded-md border border-border">
      <table class="w-full text-sm">
        <thead class="bg-bg/60 text-left text-xs uppercase tracking-wide text-muted">
          <tr>
            <th class="px-3 py-2">Stat</th>
            <th class="px-3 py-2 text-right">Baseline</th>
            <th class="px-3 py-2 text-right">Current</th>
            <th class="px-3 py-2 text-right">Δ</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border tabular-nums">
          {#each STAT_ROWS as r}
            {@const b = baseline[r.key] as number | undefined}
            {@const c = current[r.key] as number | undefined}
            <tr>
              <td class="px-3 py-1.5 text-muted">{r.label}</td>
              <td class="px-3 py-1.5 text-right">
                {r.key === "n"
                  ? (b ?? 0).toLocaleString()
                  : fmt(b)}
              </td>
              <td class="px-3 py-1.5 text-right">
                {r.key === "n"
                  ? (c ?? 0).toLocaleString()
                  : fmt(c)}
              </td>
              <td class="px-3 py-1.5 text-right text-xs {deltaTone(r.key, c, b)}">
                {r.key === "n" ? "" : pctDelta(c, b)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
