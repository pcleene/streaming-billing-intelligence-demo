<script lang="ts">
  import type { CustomerV3 } from "$lib/types";
  import { fmtMyr } from "$lib/utils";

  interface Props {
    outlets: CustomerV3[];
    parentId: string;
  }
  let { outlets, parentId }: Props = $props();

  // Derive a per-outlet revenue figure. Prefer last point of the
  // monthly_spend_trend_12m when available, fall back to current cycle.
  function revenueOf(o: CustomerV3): number {
    const t = o.cross_entity_metrics?.monthly_spend_trend_12m;
    if (t && t.length > 0) return t[t.length - 1].value;
    return o.current_cycle?.billed_amount_myr ?? o.current_cycle?.expected_amount_myr ?? 0;
  }

  const ranked = $derived(
    outlets
      .map((o) => ({ outlet: o, revenue: revenueOf(o) }))
      .sort((a, b) => b.revenue - a.revenue)
  );

  const top = $derived(ranked.slice(0, 10));
  const max = $derived(top.reduce((m, r) => Math.max(m, r.revenue), 0) || 1);
</script>

<section class="card p-5">
  <header class="mb-3 flex items-center justify-between">
    <div>
      <h2 class="text-lg font-semibold">Outlet revenue (top 10)</h2>
      <p class="text-xs text-muted">Last month spend across {outlets.length} outlets</p>
    </div>
    {#if outlets.length > 10}
      <a class="text-xs text-accent hover:underline" href={`/customers/commercial/${parentId}/outlets`}>See all</a>
    {/if}
  </header>

  {#if top.length === 0}
    <p class="text-sm text-muted">No outlet revenue available.</p>
  {:else}
    <ul class="space-y-1.5">
      {#each top as { outlet, revenue }}
        <li class="flex items-center gap-3 text-xs">
          <a class="w-44 truncate font-mono text-fg/90 hover:underline" href={`/customers/commercial/${outlet.customer_id}`}>
            {outlet.unified_profile?.name ?? outlet.customer_id}
          </a>
          <div class="flex-1 h-2 rounded-full bg-elevated">
            <div class="h-2 rounded-full bg-accent/70" style="width: {(revenue / max) * 100}%"></div>
          </div>
          <span class="w-24 text-right tabular-nums">{fmtMyr(revenue)}</span>
        </li>
      {/each}
    </ul>
  {/if}
</section>
