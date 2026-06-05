<script lang="ts">
  import { onMount } from "svelte";
  import { quarantineAssistApi, quarantineApi } from "$lib/api";
  import type { AgentTrace, AgentTraceEntry } from "$lib/types";
  import type { AgentTraceEntry as InspectorAgentTraceEntry } from "$lib/components/inspector/stores/inspector.svelte";
  import NodeFlowDiagram from "$lib/components/inspector/primitives/NodeFlowDiagram.svelte";
  import NodeAccordion from "$lib/components/inspector/primitives/NodeAccordion.svelte";
  import { Play, RotateCw } from "lucide-svelte";

  interface Props { caseId: string }
  let { caseId }: Props = $props();

  let trace = $state<AgentTrace | null>(null);
  let loading = $state(false);
  let rerunning = $state(false);
  let error = $state<string | null>(null);

  function normaliseStatus(e: AgentTraceEntry): "ok" | "error" | "skipped" {
    if (e.error) return "error";
    if ((e as Record<string, unknown>).skipped === true) return "skipped";
    if ((e as Record<string, unknown>).status === "skipped") return "skipped";
    return "ok";
  }

  const normalisedNodes = $derived.by<InspectorAgentTraceEntry[]>(() => {
    if (!trace?.trace) return [];
    return trace.trace.map((e) => ({
      node: e.node ?? "node",
      duration_ms: typeof e.duration_ms === "number" ? e.duration_ms : 0,
      status: normaliseStatus(e),
      output_keys: ((e as Record<string, unknown>).output_keys as string[] | undefined) ?? undefined,
      error: e.error ?? null,
      metadata: Object.fromEntries(
        Object.entries(e).filter(
          ([k]) => !["node", "duration_ms", "error", "output_keys", "query"].includes(k)
        )
      ),
      query: (e as Record<string, unknown>).query
    }));
  });

  // Pull mode from the classify node's metadata. The agent emits
  // `recommended_path` ("fast" | "deep") plus a free-text `rationale`;
  // older traces used `mode`/`result`/`reason`. We accept all of them
  // so re-running on a freshly-traced case lights up the badge without
  // needing a migration.
  const classifyNode = $derived(normalisedNodes.find((n) => n.node === "classify"));
  const mode = $derived<string | undefined>(
    (classifyNode?.metadata?.recommended_path as string | undefined) ??
    (classifyNode?.metadata?.mode as string | undefined) ??
    (classifyNode?.metadata?.result as string | undefined)
  );
  const classifyReason = $derived<string | undefined>(
    (classifyNode?.metadata?.rationale as string | undefined) ??
    (classifyNode?.metadata?.reason as string | undefined)
  );

  function fmtTotalDuration(ms: number | null | undefined): string {
    if (ms == null || typeof ms !== "number") return "—";
    if (ms < 1000) return `${ms.toFixed(0)} ms`;
    return `${(ms / 1000).toFixed(2)} s`;
  }

  async function load() {
    loading = true;
    try {
      trace = await quarantineAssistApi.assistTrace(caseId);
      error = null;
    } catch (e: unknown) {
      // No trace recorded yet — the user can press Re-run.
      trace = null;
      error = e instanceof Error ? e.message : "trace unavailable";
    } finally {
      loading = false;
    }
  }

  async function rerun() {
    rerunning = true;
    try {
      await quarantineApi.aiAssist(caseId, true);
      // Refresh the trace after the run completes.
      await load();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "re-run failed";
    } finally {
      rerunning = false;
    }
  }

  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-3 flex items-center justify-between gap-2">
    <div>
      <h3 class="text-sm font-semibold">LangGraph trace</h3>
      <p class="text-[11px] text-muted">
        {trace?.summary
          ? `${trace.summary.nodes_run} nodes · ${fmtTotalDuration(trace.summary.total_duration_ms)} · ${trace.summary.error_count} errors`
          : "Replay or inspect the agentic assist run."}
      </p>
    </div>
    <button
      class="btn btn-primary inline-flex items-center gap-1 text-[11px]"
      onclick={rerun}
      disabled={rerunning}
      title="Re-run AI assist with force=true"
    >
      {#if rerunning}<RotateCw size="11" class="animate-spin" /> Running…{:else}<Play size="11" /> Re-run{/if}
    </button>
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading trace…</p>
  {:else if normalisedNodes.length === 0}
    <p class="text-xs text-muted">
      {error ? `No trace yet (${error}).` : "No trace recorded for this case yet."}
      Press Re-run to invoke the agent.
    </p>
  {:else}
    <NodeFlowDiagram nodes={normalisedNodes} mode={mode} classifyReason={classifyReason} />
    <NodeAccordion nodes={normalisedNodes} />
  {/if}
</section>
