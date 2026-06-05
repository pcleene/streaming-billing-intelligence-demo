<script lang="ts">
  import type { AgentTraceEntry } from "$lib/components/inspector/stores/inspector.svelte";
  import { ChevronRight, ChevronDown } from "lucide-svelte";
  import JsonTree from "./JsonTree.svelte";

  interface Props { nodes: AgentTraceEntry[] }
  let { nodes }: Props = $props();
  let openIdx = $state<number | null>(0);
</script>

<ul class="mt-3 space-y-1.5">
  {#each nodes as n, i}
    <li class="rounded-md border border-border bg-elevated/40">
      <button
        class="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-xs"
        onclick={() => (openIdx = openIdx === i ? null : i)}
      >
        <span class="inline-flex items-center gap-1.5">
          {#if openIdx === i}<ChevronDown size="12" />{:else}<ChevronRight size="12" />{/if}
          <span class="font-mono text-fg/90">{n.node}</span>
          <span class={n.status === "ok" ? "text-ok" : n.status === "error" ? "text-danger" : "text-muted"}>
            · {n.status}
          </span>
        </span>
        <span class="text-muted">{n.duration_ms.toFixed(0)} ms</span>
      </button>
      {#if openIdx === i}
        <div class="border-t border-border/70 p-2 space-y-2">
          {#if n.output_keys && n.output_keys.length}
            <div class="text-[11px]">
              <span class="text-muted">output_keys:</span>
              <span class="font-mono">[{n.output_keys.join(", ")}]</span>
            </div>
          {/if}
          {#if n.error}
            <div class="text-[11px] text-danger">{n.error}</div>
          {/if}
          {#if n.query !== undefined}
            <div>
              <div class="text-[10px] uppercase tracking-wide text-muted mb-1">Underlying query</div>
              <JsonTree value={n.query} />
            </div>
          {/if}
          {#if n.metadata && Object.keys(n.metadata).length > 0}
            <div>
              <div class="text-[10px] uppercase tracking-wide text-muted mb-1">Metadata</div>
              <JsonTree value={n.metadata} />
            </div>
          {/if}
        </div>
      {/if}
    </li>
  {/each}
</ul>
