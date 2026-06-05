<script lang="ts">
  import type { EntityHealthSummary } from "$lib/api";
  import { fmtMyr } from "$lib/utils";

  interface Props {
    data: EntityHealthSummary | null;
    loading?: boolean;
  }
  let { data, loading = false }: Props = $props();

  function bandClass(b: string): string {
    if (b === "high") return "pill pill-danger text-[10px]";
    if (b === "medium") return "pill pill-warn text-[10px]";
    if (b === "low") return "pill pill-ok text-[10px]";
    return "pill pill-muted text-[10px]";
  }
</script>

<section class="card p-5">
  <header class="mb-3">
    <h2 class="text-lg font-semibold">Per-entity health</h2>
    <p class="text-xs text-muted">Subscribers, MRR, churn band</p>
  </header>

  {#if loading}
    <p class="text-sm text-muted">Loading…</p>
  {:else if !data || !data.rows || data.rows.length === 0}
    <p class="text-sm text-muted">No entity health summary available.</p>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
      {#each data.rows as r}
        <div class="rounded-lg border border-border bg-elevated/40 p-3">
          <div class="text-[11px] font-mono text-muted">{r.entity}</div>
          <div class="mt-1 text-lg font-semibold">{r.subscriber_count.toLocaleString()}</div>
          <div class="text-xs text-muted">{fmtMyr(r.mrr_myr)} MRR</div>
          <div class="mt-1.5"><span class={bandClass(r.churn_band)}>churn {r.churn_band}</span></div>
        </div>
      {/each}
    </div>
  {/if}
</section>
