<script lang="ts">
  import { inspector, type InspectorTab } from "./stores/inspector.svelte";

  const tabs: { id: InspectorTab; label: string; hint: string }[] = [
    { id: "query", label: "Query", hint: "1" },
    { id: "result", label: "Result", hint: "2" },
    { id: "trace", label: "Agent Trace", hint: "3" },
    { id: "explain", label: "Explain", hint: "4" }
  ];

  const hasTrace = $derived((inspector.payload?.agent_trace?.length ?? 0) > 0);
</script>

<nav class="flex items-stretch border-b border-border bg-surface">
  {#each tabs as t}
    {@const hidden = t.id === "trace" && !hasTrace}
    {#if !hidden}
      <button
        class={`px-3 py-2 text-xs border-b-2 transition-colors ${
          inspector.activeTab === t.id
            ? "border-accent text-accent"
            : "border-transparent text-muted hover:text-fg"
        }`}
        onclick={() => inspector.switchTab(t.id)}
        title={`Tab ${t.hint}`}
      >
        {t.label}
        <span class="ml-1 text-[10px] opacity-60">{t.hint}</span>
      </button>
    {/if}
  {/each}
</nav>
