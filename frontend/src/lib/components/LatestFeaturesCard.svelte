<script lang="ts">
  import type { LatestFeaturesSummary } from "$lib/api";
  import { fmtRelative } from "$lib/utils";

  interface Props {
    data: LatestFeaturesSummary | null;
    loading?: boolean;
  }
  let { data, loading = false }: Props = $props();
</script>

<section class="card p-5">
  <header class="mb-3">
    <h2 class="text-lg font-semibold">Latest online features</h2>
    <p class="text-xs text-muted">Sub-minute feature store snapshot</p>
  </header>

  {#if loading}
    <p class="text-sm text-muted">Loading…</p>
  {:else if !data || data.items.length === 0}
    <p class="text-sm text-muted">No features available.</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.items as item}
        <li class="flex items-center gap-3 py-2 text-sm">
          <span class="font-mono text-xs text-fg/80">{item.feature_name}</span>
          <span class="ml-auto text-right tabular-nums">
            {item.value}{item.unit ? ` ${item.unit}` : ""}
          </span>
          {#if item.updated_at}
            <span class="ml-2 text-[11px] text-muted">{fmtRelative(item.updated_at)}</span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>
