<script lang="ts">
  import type { InspectorPayload } from "$lib/components/inspector/stores/inspector.svelte";
  import PipelineStageChip from "$lib/components/inspector/primitives/PipelineStageChip.svelte";
  import JsonTree from "$lib/components/inspector/primitives/JsonTree.svelte";
  import { Copy } from "lucide-svelte";

  interface Props { payload: InspectorPayload | null }
  let { payload }: Props = $props();

  const pipelineStages = $derived.by<{ stage: string; forbidden: boolean }[]>(() => {
    if (!payload) return [];
    const q = payload.query;
    if (Array.isArray(q)) {
      return q.flatMap((stage) => {
        if (stage && typeof stage === "object") {
          return Object.keys(stage as object).map((k) => ({
            stage: k.startsWith("$") ? k : `$${k}`,
            forbidden: k === "$lookup" || k === "lookup"
          }));
        }
        return [];
      });
    }
    return [];
  });

  const callout = $derived.by<string | null>(() => {
    if (!payload) return null;
    const q = payload.query as unknown;
    if (payload.operation === "find_one" && q && typeof q === "object" && "customer_id" in (q as object)) {
      return "Single document fetch — every dependency this page renders is already embedded in this document.";
    }
    if (Array.isArray(q) && q.some((s) => s && typeof s === "object" && "$vectorSearch" in (s as object))) {
      return "AutoEmbed converted this text to a vector using voyage-4-large before searching.";
    }
    return null;
  });

  function copyMongosh() {
    if (!payload) return;
    const dbColl = `db.${payload.collection}`;
    let script: string;
    if (Array.isArray(payload.query)) {
      script = `${dbColl}.aggregate(${JSON.stringify(payload.query, null, 2)})`;
    } else if (payload.operation === "find_one") {
      script = `${dbColl}.findOne(${JSON.stringify(payload.query, null, 2)})`;
    } else {
      script = `${dbColl}.${payload.operation}(${JSON.stringify(payload.query, null, 2)})`;
    }
    try {
      navigator.clipboard.writeText(script);
    } catch {
      /* clipboard may be unavailable in some browsers */
    }
  }
</script>

{#if !payload}
  <p class="text-sm text-muted">No query recorded yet. Trigger a fetch with the inspector open.</p>
{:else}
  <div class="space-y-3">
    <div class="flex items-center justify-between gap-2">
      <div class="text-[11px] font-mono text-muted">
        {payload.database}.{payload.collection} · {payload.operation}
      </div>
      <button
        class="btn btn-primary text-[11px] inline-flex items-center gap-1"
        onclick={copyMongosh}
        title="Copy as mongosh"
      >
        <Copy size="11" /> mongosh
      </button>
    </div>

    {#if pipelineStages.length > 0}
      <div class="flex flex-wrap gap-1.5">
        {#each pipelineStages as s}
          <PipelineStageChip stage={s.stage} forbidden={s.forbidden} />
        {/each}
      </div>
    {/if}

    {#if callout}
      <div class="rounded-md border border-accent/30 bg-accent/10 p-2 text-[11px] text-accent">
        {callout}
      </div>
    {/if}

    <div class="rounded-md border border-border bg-elevated/40 p-2 overflow-auto max-h-[60vh]">
      <JsonTree value={payload.query} />
    </div>
  </div>
{/if}
