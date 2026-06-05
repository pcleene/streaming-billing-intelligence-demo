<script lang="ts">
  import type { InspectorPayload } from "$lib/components/inspector/stores/inspector.svelte";
  import IndexBadge from "$lib/components/inspector/primitives/IndexBadge.svelte";

  interface Props { payload: InspectorPayload | null }
  let { payload }: Props = $props();

  const ex = $derived(payload?.explain ?? null);
  const ratio = $derived(
    ex && ex.totalDocsExamined && ex.nReturned != null
      ? (ex.nReturned / Math.max(1, ex.totalDocsExamined))
      : null
  );
  const optimal = $derived(
    ex != null
      && (ex.stage === "IXSCAN" || ex.stage === "VECTOR_SEARCH")
      && (ratio == null || ratio >= 0.99)
      && (ex.executionTimeMillis ?? 0) < 50
  );
  const inefficient = $derived(
    ex != null && (ex.stage === "COLLSCAN" || (ratio != null && ratio < 0.5))
  );
</script>

{#if !payload}
  <p class="text-sm text-muted">No payload.</p>
{:else if !ex}
  <p class="text-sm text-muted">No explain captured. Use <code class="font-mono">?inspect=full</code> for heavy aggregations.</p>
{:else}
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <IndexBadge stage={ex.stage} indexName={ex.index_name ?? payload.index_name} />
      {#if optimal}
        <span class="pill pill-ok text-[10px]">Optimal</span>
      {:else if inefficient}
        <span class="pill pill-danger text-[10px]" title="Possibly missing an index — see setup_indexes.py">
          Inefficient — missing index?
        </span>
      {/if}
    </div>

    <dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
      {#if ex.nReturned != null}
        <dt class="text-muted">nReturned</dt><dd class="font-mono">{ex.nReturned}</dd>
      {/if}
      {#if ex.totalDocsExamined != null}
        <dt class="text-muted">totalDocsExamined</dt><dd class="font-mono">{ex.totalDocsExamined}</dd>
      {/if}
      {#if ratio != null}
        <dt class="text-muted">selectivity</dt><dd class="font-mono">{(ratio * 100).toFixed(1)}%</dd>
      {/if}
      {#if ex.executionTimeMillis != null}
        <dt class="text-muted">executionTimeMillis</dt><dd class="font-mono">{ex.executionTimeMillis}</dd>
      {/if}
      {#if ex.model}
        <dt class="text-muted">model</dt><dd class="font-mono">{ex.model}</dd>
      {/if}
      {#if ex.dimensions != null}
        <dt class="text-muted">dimensions</dt><dd class="font-mono">{ex.dimensions}</dd>
      {/if}
      {#if ex.similarity}
        <dt class="text-muted">similarity</dt><dd class="font-mono">{ex.similarity}</dd>
      {/if}
      {#if ex.num_candidates != null}
        <dt class="text-muted">numCandidates</dt><dd class="font-mono">{ex.num_candidates}</dd>
      {/if}
      {#if ex.candidates_evaluated != null}
        <dt class="text-muted">candidatesEvaluated</dt><dd class="font-mono">{ex.candidates_evaluated}</dd>
      {/if}
    </dl>
  </div>
{/if}
