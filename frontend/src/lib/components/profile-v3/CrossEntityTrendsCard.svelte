<script lang="ts">
  import type { CrossEntityMetrics, TrendPoint } from "$lib/types";
  import Sparkline from "./Sparkline.svelte";
  import { fmtMyr } from "$lib/utils";
  import { TrendingUp, TrendingDown, Minus } from "lucide-svelte";

  interface Props { metrics: CrossEntityMetrics }
  let { metrics }: Props = $props();

  function pts(t?: TrendPoint[]): number[] {
    return (t ?? []).map((p) => p.value);
  }
  function delta(t?: TrendPoint[]): number {
    if (!t || t.length < 2) return 0;
    return t[t.length - 1].value - t[0].value;
  }
  function deltaPct(t?: TrendPoint[]): string {
    if (!t || t.length < 2 || !t[0].value) return "—";
    const d = (t[t.length - 1].value - t[0].value) / Math.abs(t[0].value);
    const sign = d > 0 ? "+" : "";
    return `${sign}${(d * 100).toFixed(0)}%`;
  }

  const trends = $derived([
    {
      label: "LTV (12m)",
      points: pts(metrics.ltv_trend_12m),
      d: delta(metrics.ltv_trend_12m),
      pct: deltaPct(metrics.ltv_trend_12m),
      fmt: (v: number) => fmtMyr(v)
    },
    {
      label: "Monthly spend (12m)",
      points: pts(metrics.monthly_spend_trend_12m),
      d: delta(metrics.monthly_spend_trend_12m),
      pct: deltaPct(metrics.monthly_spend_trend_12m),
      fmt: (v: number) => fmtMyr(v)
    },
    {
      label: "Viewing hours (12m)",
      points: pts(metrics.viewing_hours_trend_12m),
      d: delta(metrics.viewing_hours_trend_12m),
      pct: deltaPct(metrics.viewing_hours_trend_12m),
      fmt: (v: number) => `${v.toFixed(0)}h`
    }
  ]);
</script>

<section class="card p-5">
  <header class="mb-4">
    <h2 class="text-lg font-semibold">Cross-entity trends</h2>
    <p class="text-xs text-muted">12-month rollups from <span class="font-mono">cross_entity_metrics</span></p>
  </header>

  <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
    {#each trends as t}
      {@const last = t.points.length ? t.points[t.points.length - 1] : null}
      {@const Icon = t.d > 0 ? TrendingUp : t.d < 0 ? TrendingDown : Minus}
      {@const color = t.d > 0 ? "text-ok" : t.d < 0 ? "text-danger" : "text-muted"}
      <div>
        <div class="flex items-center justify-between text-xs text-muted">
          <span>{t.label}</span>
          <span class={color + " flex items-center gap-1"}>
            <Icon size="12" />
            {t.pct}
          </span>
        </div>
        <div class="mt-1 text-lg font-semibold">
          {last != null ? t.fmt(last) : "—"}
        </div>
        <Sparkline points={t.points} width={220} height={32} ariaLabel={t.label} />
      </div>
    {/each}
  </div>

  <div class="mt-4 grid grid-cols-3 gap-3 text-xs">
    <div class="rounded-md border border-border bg-elevated/50 p-2.5">
      <div class="text-muted">Total LTV</div>
      <div class="text-fg font-semibold">{fmtMyr(metrics.total_ltv_myr)}</div>
    </div>
    <div class="rounded-md border border-border bg-elevated/50 p-2.5">
      <div class="text-muted">Cross-sell</div>
      <div class="text-fg font-semibold">{(metrics.cross_sell_score * 100).toFixed(0)}%</div>
    </div>
    <div class="rounded-md border border-border bg-elevated/50 p-2.5">
      <div class="text-muted">Churn risk</div>
      <div class:text-danger={metrics.churn_risk > 0.5} class:text-warn={metrics.churn_risk > 0.25 && metrics.churn_risk <= 0.5} class:text-ok={metrics.churn_risk <= 0.25} class="font-semibold">
        {(metrics.churn_risk * 100).toFixed(0)}%
      </div>
    </div>
  </div>
</section>
