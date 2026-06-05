<script lang="ts">
  import type { AgentTraceEntry } from "$lib/components/inspector/stores/inspector.svelte";
  import { CheckCircle2, XCircle, MinusCircle, Zap, Telescope } from "lucide-svelte";

  interface Props {
    nodes: AgentTraceEntry[];
    /** "fast" or "deep" — for the mode badge. */
    mode?: string;
    classifyReason?: string;
  }
  let { nodes, mode, classifyReason }: Props = $props();

  function ringFor(status: string): string {
    switch (status) {
      case "ok": return "border-ok/60";
      case "error": return "border-danger/70";
      case "skipped": return "border-border border-dashed";
      default: return "border-border";
    }
  }
  function StatusIcon(status: string) {
    if (status === "ok") return CheckCircle2;
    if (status === "error") return XCircle;
    return MinusCircle;
  }
</script>

<div class="rounded-md border border-border bg-elevated/40 p-3">
  <div class="mb-2 flex items-center justify-between">
    <div class="text-[11px] uppercase tracking-wide text-muted">Agent flow</div>
    {#if mode}
      <span
        class={`pill text-[10px] inline-flex items-center gap-1 border ${mode === "deep" ? "bg-accent/15 text-accent border-accent/30" : "bg-ok/15 text-ok border-ok/30"}`}
        title={classifyReason ?? ""}
      >
        {#if mode === "deep"}<Telescope size="11" />{:else}<Zap size="11" />{/if}
        {mode}
      </span>
    {/if}
  </div>

  <div class="flex flex-wrap items-stretch gap-2">
    {#each nodes as n, i}
      {@const Icon = StatusIcon(n.status)}
      <div class={`min-w-[120px] flex-1 rounded-md border bg-surface p-2 ${ringFor(n.status)}`}>
        <div class="flex items-center justify-between">
          <span class="font-mono text-[11px] text-fg/90">{n.node}</span>
          <Icon size="12" class={n.status === "ok" ? "text-ok" : n.status === "error" ? "text-danger" : "text-muted"} />
        </div>
        <div class="mt-1 text-[10px] text-muted">{n.duration_ms.toFixed(0)} ms</div>
      </div>
      {#if i < nodes.length - 1}
        <div class="flex items-center text-muted">→</div>
      {/if}
    {/each}
    {#if nodes.length === 0}
      <span class="text-xs text-muted">No agent trace recorded.</span>
    {/if}
  </div>
</div>
