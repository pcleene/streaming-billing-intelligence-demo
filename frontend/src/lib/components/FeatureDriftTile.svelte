<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { featuresApi, type FeatureDriftItem } from "$lib/api";

  interface Props {
    refreshSeconds?: number;
    top?: number;
  }
  let { refreshSeconds = 60, top = 8 }: Props = $props();

  let items: FeatureDriftItem[] = $state([]);
  let loading = $state(true);
  let err: string | null = $state(null);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      const r = await featuresApi.drift(top);
      items = r.items;
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

  function severityClass(sev: FeatureDriftItem["severity"]): string {
    return sev === "alert"
      ? "text-danger"
      : sev === "warn"
        ? "text-warn"
        : sev === "watch"
          ? "text-accent"
          : "text-muted";
  }
</script>

<div class="card p-4">
  <div class="flex items-center justify-between">
    <div class="text-xs uppercase tracking-wide text-muted">Feature drift</div>
    <div class="text-xs text-muted">top {top} · KS desc</div>
  </div>

  {#if loading}
    <div class="mt-3 text-sm text-muted">Loading…</div>
  {:else if err}
    <div class="mt-3 text-sm text-danger">{err}</div>
  {:else if items.length === 0}
    <div class="mt-3 text-sm text-muted">
      No drift samples yet — run <code>make drift-detector</code>.
    </div>
  {:else}
    <ul class="mt-2 divide-y divide-border text-sm">
      {#each items as it}
        <li class="flex items-center gap-3 py-2">
          <span class="font-mono text-xs">{it.feature_name}</span>
          <span class="ml-auto text-xs {severityClass(it.severity)}">{it.severity}</span>
          <span class="w-12 text-right tabular-nums">{it.ks_statistic.toFixed(2)}</span>
        </li>
      {/each}
    </ul>
  {/if}
</div>
