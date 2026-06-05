<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { inspector } from "./stores/inspector.svelte";
  import InspectorTabs from "./InspectorTabs.svelte";
  import InspectorFooter from "./InspectorFooter.svelte";
  import QueryTab from "./tabs/QueryTab.svelte";
  import ResultTab from "./tabs/ResultTab.svelte";
  import AgentTraceTab from "./tabs/AgentTraceTab.svelte";
  import ExplainTab from "./tabs/ExplainTab.svelte";
  import { X, Pin, PinOff } from "lucide-svelte";

  function onKey(e: KeyboardEvent) {
    // Ignore typing inside form fields.
    const target = e.target as HTMLElement | null;
    if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
    if (target && target.isContentEditable) return;

    if (e.key === "?" && !inspector.open) {
      e.preventDefault();
      inspector.openPanel();
      return;
    }
    if (!inspector.open) return;
    if (e.key === "Escape") {
      e.preventDefault();
      inspector.forceClose();
    } else if (e.key === "p") {
      e.preventDefault();
      inspector.togglePin();
    } else if (e.key === "1") {
      inspector.switchTab("query");
    } else if (e.key === "2") {
      inspector.switchTab("result");
    } else if (e.key === "3") {
      inspector.switchTab("trace");
    } else if (e.key === "4") {
      inspector.switchTab("explain");
    } else if (e.key === "c") {
      // Copy currently-active tab content.
      try {
        const text = inspector.payload
          ? inspector.activeTab === "query"
            ? JSON.stringify(inspector.payload.query, null, 2)
            : inspector.activeTab === "result"
              ? JSON.stringify(inspector.payload.documents, null, 2)
              : inspector.activeTab === "explain"
                ? JSON.stringify(inspector.payload.explain, null, 2)
                : JSON.stringify(inspector.payload.agent_trace, null, 2)
          : "";
        if (text) navigator.clipboard.writeText(text);
      } catch { /* ignore */ }
    }
  }

  onMount(() => {
    window.addEventListener("keydown", onKey);
  });
  onDestroy(() => {
    if (typeof window !== "undefined") window.removeEventListener("keydown", onKey);
  });
</script>

{#if inspector.open}
  <!-- backdrop -->
  <div
    class="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
    onclick={() => inspector.close()}
    aria-hidden="true"
  ></div>

  <!-- slide-over -->
  <aside
    class="fixed right-0 top-0 z-50 h-full w-full sm:w-[640px] bg-surface shadow-2xl border-l border-border flex flex-col"
    role="dialog"
    aria-label="MongoDB Inspector"
  >
    <header class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
      <div class="flex items-center gap-2">
        <span class="text-xs font-semibold uppercase tracking-wide text-muted">MongoDB Inspector</span>
        {#if inspector.payload}
          <span class="pill bg-elevated text-fg border border-border font-mono text-[10px]">
            {inspector.payload.database}.{inspector.payload.collection}
          </span>
        {/if}
      </div>
      <div class="flex items-center gap-1">
        <button
          class="btn p-1.5 text-muted hover:text-fg"
          onclick={() => inspector.togglePin()}
          title={inspector.pinned ? "Unpin (p)" : "Pin (p)"}
        >
          {#if inspector.pinned}<PinOff size="14" />{:else}<Pin size="14" />{/if}
        </button>
        <button
          class="btn p-1.5 text-muted hover:text-fg"
          onclick={() => inspector.forceClose()}
          title="Close (Esc)"
        >
          <X size="14" />
        </button>
      </div>
    </header>

    <InspectorTabs />

    <div class="flex-1 overflow-auto p-3">
      {#if inspector.activeTab === "query"}
        <QueryTab payload={inspector.payload} />
      {:else if inspector.activeTab === "result"}
        <ResultTab payload={inspector.payload} />
      {:else if inspector.activeTab === "trace"}
        <AgentTraceTab payload={inspector.payload} />
      {:else}
        <ExplainTab payload={inspector.payload} />
      {/if}

      {#if !inspector.payload}
        <div class="mt-4 rounded-md border border-border bg-elevated/40 p-3 text-xs text-muted">
          Open the inspector while navigating, or press <kbd class="font-mono">?</kbd> on any page.
          Pages with inspector wiring will re-fetch with <code class="font-mono">?inspect=true</code>
          to record query, documents, and the agent trace.
        </div>
      {/if}
    </div>

    <InspectorFooter />
  </aside>
{/if}
