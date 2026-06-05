<script lang="ts">
  import { onMount } from "svelte";
  import { polishApi } from "$lib/api";
  import { Timer } from "lucide-svelte";

  let data = $state<{ median_minutes: number | null; p95_minutes: number | null; sample: number } | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      data = await polishApi.resolutionVelocity();
      error = null;
    } catch (e: unknown) {
      data = null;
      error = e instanceof Error ? e.message : "velocity unavailable";
    } finally {
      loading = false;
    }
  }

  function fmt(m: number | null | undefined) {
    if (m == null) return "—";
    if (m >= 60) return `${(m / 60).toFixed(1)} h`;
    return `${m.toFixed(0)} m`;
  }

  // Tone the median: < 30m good, < 120m ok, else warn.
  const tone = $derived(
    data?.median_minutes == null
      ? "muted"
      : data.median_minutes < 30
        ? "ok"
        : data.median_minutes < 120
          ? "accent"
          : "warn"
  );
  const toneCls = $derived(
    tone === "ok" ? "text-ok" :
    tone === "accent" ? "text-accent" :
    tone === "warn" ? "text-warn" : "text-muted"
  );

  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-3 flex items-center justify-between">
    <div class="flex items-center gap-2">
      <Timer size="14" class="text-accent" />
      <h3 class="text-sm font-semibold">Resolution velocity</h3>
    </div>
    {#if data}<span class="text-[11px] text-muted">n={data.sample}</span>{/if}
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading…</p>
  {:else if error}
    <p class="text-xs text-muted">{error}</p>
  {:else if data}
    <div class="grid grid-cols-2 gap-3">
      <div>
        <div class="text-[10px] uppercase tracking-wide text-muted">Median</div>
        <div class={`text-2xl font-semibold ${toneCls}`}>{fmt(data.median_minutes)}</div>
      </div>
      <div>
        <div class="text-[10px] uppercase tracking-wide text-muted">p95</div>
        <div class="text-2xl font-semibold">{fmt(data.p95_minutes)}</div>
      </div>
    </div>
  {:else}
    <p class="text-xs text-muted">No data.</p>
  {/if}
</section>
