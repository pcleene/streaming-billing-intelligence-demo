<script lang="ts">
  import type { InspectorPayload } from "$lib/components/inspector/stores/inspector.svelte";
  import JsonTree from "$lib/components/inspector/primitives/JsonTree.svelte";

  interface Props { payload: InspectorPayload | null }
  let { payload }: Props = $props();

  type Mode = "tree" | "raw" | "schema";
  let mode = $state<Mode>("tree");

  const single = $derived(
    payload && payload.documents && payload.documents.length === 1
      ? payload.documents[0]
      : null
  );

  function schemaRows(doc: unknown): { key: string; type: string; sample: string; size: number }[] {
    if (!doc || typeof doc !== "object") return [];
    const rows: { key: string; type: string; sample: string; size: number }[] = [];
    for (const [k, v] of Object.entries(doc as Record<string, unknown>)) {
      let type: string = typeof v;
      if (v === null) type = "null";
      else if (Array.isArray(v)) type = `array(${v.length})`;
      else if (type === "object") type = "object";
      let sample = "";
      try { sample = JSON.stringify(v); } catch { sample = String(v); }
      const size = sample.length;
      if (sample.length > 60) sample = sample.slice(0, 60) + "…";
      rows.push({ key: k, type, sample, size });
    }
    return rows.sort((a, b) => b.size - a.size);
  }
</script>

{#if !payload}
  <p class="text-sm text-muted">No documents recorded yet.</p>
{:else}
  <div class="space-y-3">
    <div class="flex items-center justify-between gap-2">
      <div class="text-[11px] text-muted">
        {payload.result_count} document{payload.result_count === 1 ? "" : "s"}
        · {(payload.result_bytes / 1024).toFixed(1)} KB
      </div>
      <div class="inline-flex rounded-md border border-border overflow-hidden text-[11px]">
        {#each ["tree", "raw", "schema"] as m}
          <button
            class={`px-2 py-0.5 ${mode === m ? "bg-accent/15 text-accent" : "text-muted hover:text-fg"}`}
            onclick={() => (mode = m as Mode)}
          >
            {m}
          </button>
        {/each}
      </div>
    </div>

    <div class="rounded-md border border-border bg-elevated/40 p-2 overflow-auto max-h-[60vh]">
      {#if mode === "tree"}
        {#if single}
          <JsonTree value={single} />
        {:else}
          <JsonTree value={payload.documents} />
        {/if}
      {:else if mode === "raw"}
        <pre class="text-[11px] leading-snug font-mono whitespace-pre-wrap">{JSON.stringify(payload.documents, null, 2)}</pre>
      {:else}
        <table class="w-full text-[11px]">
          <thead class="text-muted">
            <tr><th class="text-left py-1">Key</th><th class="text-left">Type</th><th class="text-left">Sample</th><th class="text-right">Bytes</th></tr>
          </thead>
          <tbody>
            {#each schemaRows(single ?? payload.documents[0] ?? {}) as r}
              <tr class="border-t border-border/60">
                <td class="py-1 font-mono text-fg/90">{r.key}</td>
                <td class="text-muted">{r.type}</td>
                <td class="font-mono text-fg/80 truncate max-w-[180px]">{r.sample}</td>
                <td class="text-right text-muted">{r.size}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  </div>
{/if}
