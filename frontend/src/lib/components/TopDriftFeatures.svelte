<script lang="ts">
  import { onMount } from "svelte";
  import { polishApi } from "$lib/api";
  import type { TopDriftItem } from "$lib/api";
  import { TrendingUp } from "lucide-svelte";

  interface Props { top?: number }
  let { top = 5 }: Props = $props();

  let items = $state<TopDriftItem[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      const r = await polishApi.topDrift(top);
      items = r.items ?? [];
      error = null;
    } catch (e: unknown) {
      items = [];
      error = e instanceof Error ? e.message : "top-drift unavailable";
    } finally {
      loading = false;
    }
  }

  function sevPill(s: TopDriftItem["severity"]) {
    switch (s) {
      case "alert": return "pill-danger";
      case "warn": return "pill-warn";
      case "watch": return "pill-accent";
      default: return "pill-muted";
    }
  }

  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-3 flex items-center justify-between">
    <div class="flex items-center gap-2">
      <TrendingUp size="14" class="text-accent" />
      <h3 class="text-sm font-semibold">Top drifting features</h3>
    </div>
    <span class="text-[11px] text-muted">top {top}</span>
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading…</p>
  {:else if error}
    <p class="text-xs text-muted">{error}</p>
  {:else if items.length === 0}
    <p class="text-xs text-muted">No drift detected.</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each items as it}
        <li class="flex items-center gap-2 py-2 text-sm">
          <span class={`pill ${sevPill(it.severity)} text-[10px] uppercase`}>{it.severity}</span>
          <a class="font-mono text-accent hover:underline truncate" href={`/features/${encodeURIComponent(it.feature_name)}`}>
            {it.feature_name}
          </a>
          <span class="ml-auto text-xs text-muted tabular-nums">KS {it.ks_statistic.toFixed(3)}</span>
          <span class="text-[10px] text-muted tabular-nums">{it.affected_consumers_count} consumers</span>
        </li>
      {/each}
    </ul>
  {/if}
</section>
