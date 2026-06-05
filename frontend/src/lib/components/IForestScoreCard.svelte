<script lang="ts">
  import { onMount } from "svelte";
  import { iforestApi, type IForestScore } from "$lib/api";
  import { RotateCw, Activity } from "lucide-svelte";

  interface Props { customerId: string }
  let { customerId }: Props = $props();

  let score = $state<IForestScore | null>(null);
  let loading = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      score = await iforestApi.get(customerId);
      error = null;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "score unavailable";
      score = null;
    } finally {
      loading = false;
    }
  }

  async function rescore() {
    busy = true;
    try {
      score = await iforestApi.rescore(customerId);
      error = null;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "rescore failed";
    } finally {
      busy = false;
    }
  }

  const tone = $derived(
    score && score.score > 0.7 ? "danger" :
    score && score.score > 0.5 ? "warn" : "ok"
  );

  const toneClass = $derived(
    tone === "ok" ? "text-ok" :
    tone === "warn" ? "text-warn" : "text-danger"
  );

  // Tiny vanilla SVG histogram for the cluster distribution.
  const dist = $derived(score?.cluster_distribution ?? []);
  const maxCount = $derived(Math.max(1, ...dist.map((b) => b.count)));
  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-2 flex items-center justify-between gap-2">
    <div class="flex items-center gap-2">
      <Activity size="14" class={toneClass} />
      <h3 class="text-sm font-semibold">Anomaly score</h3>
    </div>
    <button class="btn text-[11px] inline-flex items-center gap-1" onclick={rescore} disabled={busy}>
      <RotateCw size="11" class={busy ? "animate-spin" : ""} /> Re-score
    </button>
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading…</p>
  {:else if error}
    <p class="text-xs text-muted">{error}</p>
  {:else if score}
    <div class="flex items-baseline gap-2">
      <span class={`text-2xl font-semibold ${toneClass}`}>{score.score.toFixed(2)}</span>
      <span class="text-[11px] text-muted">model {score.model_version}</span>
    </div>
    <p class="text-[11px] text-muted">Scored {score.scored_at}{score.cluster ? ` · cluster ${score.cluster}` : ""}</p>

    {#if dist.length > 0}
      <div class="mt-3">
        <div class="mb-1 text-[10px] uppercase tracking-wide text-muted">Cluster distribution</div>
        <svg viewBox={`0 0 ${Math.max(120, dist.length * 14)} 36`} class="w-full">
          {#each dist as b, i}
            {@const h = (b.count / maxCount) * 32}
            <rect
              x={i * 14 + 1}
              y={34 - h}
              width="10"
              height={h}
              class="fill-accent/70"
            >
              <title>{b.bucket}: {b.count}</title>
            </rect>
          {/each}
        </svg>
      </div>
    {/if}
  {:else}
    <p class="text-xs text-muted">No score yet.</p>
  {/if}
</section>
