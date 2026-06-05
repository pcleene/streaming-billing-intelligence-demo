<script lang="ts">
  import type { NetNewRevenueSummary } from "$lib/api";
  import Sparkline from "$lib/components/profile-v3/Sparkline.svelte";
  import { fmtMyr } from "$lib/utils";

  interface Props {
    data: NetNewRevenueSummary | null;
    loading?: boolean;
    /** Bound to the window selector so the parent can refetch on change. */
    hours?: number;
    onHoursChange?: (h: number) => void;
  }
  let {
    data,
    loading = false,
    hours = 1,
    onHoursChange
  }: Props = $props();

  const WINDOWS = [1, 4];

  function pick(h: number) {
    if (onHoursChange) onHoursChange(h);
  }
</script>

<section class="card p-5">
  <header class="mb-3 flex items-start justify-between gap-3">
    <div>
      <h2 class="text-lg font-semibold">Cross-entity net new revenue</h2>
      <p class="text-xs text-muted">
        Transactions ingested in the last {hours}h, grouped by entity
      </p>
    </div>
    <div class="flex gap-1" role="tablist" aria-label="Window">
      {#each WINDOWS as w}
        <button
          type="button"
          class="rounded px-2 py-0.5 text-xs {hours === w
            ? 'bg-accent text-white'
            : 'border border-border text-muted hover:text-fg'}"
          aria-pressed={hours === w}
          onclick={() => pick(w)}
        >
          {w}h
        </button>
      {/each}
    </div>
  </header>

  {#if loading}
    <p class="text-sm text-muted">Loading…</p>
  {:else if !data || !data.rows || data.rows.length === 0}
    <p class="text-sm text-muted">
      No transactions in the last {hours}h. Run the simulator to populate.
    </p>
  {:else}
    <div class="mb-4 flex items-baseline gap-4">
      <div>
        <div class="text-xs uppercase tracking-wide text-muted">Total ({hours}h)</div>
        <div class="text-2xl font-semibold">{fmtMyr(data.total_myr)}</div>
      </div>
      <div class="text-xs text-muted">
        {data.count.toLocaleString()} transactions
      </div>
    </div>
    <ul class="space-y-2">
      {#each data.rows as r}
        <li class="flex items-center gap-3">
          <span class="w-36 truncate font-mono text-xs text-fg/90">{r.entity}</span>
          <div class="flex-1">
            <Sparkline
              points={(r.trend ?? []).map((p) => p.value_myr)}
              width={220}
              height={20}
            />
          </div>
          <span class="w-12 text-right text-xs tabular-nums text-muted">
            {r.count}
          </span>
          <span class="w-24 text-right text-xs tabular-nums">{fmtMyr(r.total_myr)}</span>
        </li>
      {/each}
    </ul>
  {/if}
</section>
