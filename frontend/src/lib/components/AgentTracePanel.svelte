<script lang="ts">
  import { quarantineAssistApi } from "$lib/api";
  import type { AgentTrace, AgentTraceEntry } from "$lib/types";
  import JsonView from "./JsonView.svelte";
  import KpiTile from "./KpiTile.svelte";
  import { Activity, Clock, AlertCircle, ChevronDown, ChevronRight } from "lucide-svelte";

  interface Props {
    caseId: string;
  }
  let { caseId }: Props = $props();

  let collapsed = $state(true);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let data = $state<AgentTrace | null>(null);
  let expandedRows = $state<Record<number, boolean>>({});

  async function load() {
    if (!caseId) return;
    loading = true;
    error = null;
    try {
      data = await quarantineAssistApi.assistTrace(caseId);
    } catch (e: unknown) {
      data = null;
      error = e instanceof Error ? e.message : "trace load failed";
    } finally {
      loading = false;
    }
  }

  function toggle() {
    collapsed = !collapsed;
    if (!collapsed && !data && !loading) {
      load();
    }
  }

  function entryNode(entry: AgentTraceEntry): string {
    return entry.node ?? "(unknown)";
  }

  function entryDuration(entry: AgentTraceEntry): string {
    const d = entry.duration_ms;
    if (d == null || typeof d !== "number") return "—";
    if (d < 1000) return `${d.toFixed(0)} ms`;
    return `${(d / 1000).toFixed(2)} s`;
  }

  function fmtTotalDuration(ms: number | null): string {
    if (ms == null) return "—";
    if (ms < 1000) return `${ms.toFixed(0)} ms`;
    return `${(ms / 1000).toFixed(2)} s`;
  }
</script>

<div class="card-elevated p-5">
  <header class="flex items-center gap-2">
    <Activity size="16" class="text-accent" />
    <h3 class="text-sm font-semibold uppercase tracking-wide">Agent trace</h3>
    <button
      type="button"
      class="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-fg"
      onclick={toggle}
      aria-expanded={!collapsed}
    >
      {#if collapsed}
        <ChevronRight size="14" />
        <span>show</span>
      {:else}
        <ChevronDown size="14" />
        <span>hide</span>
      {/if}
    </button>
  </header>

  {#if !collapsed}
    <div class="mt-4">
      {#if loading}
        <p class="text-sm text-muted">Loading agent trace…</p>
      {:else if error}
        <p class="text-sm text-danger">{error}</p>
      {:else if !data}
        <p class="text-sm text-muted">No trace loaded yet.</p>
      {:else if !data.has_trace}
        <div class="rounded-lg border border-border bg-elevated p-4 text-sm text-muted">
          <p>No agent trace recorded for this case.</p>
          <p class="mt-1 text-xs">Run "Ask AI Assist" to produce a trace.</p>
        </div>
      {:else}
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <KpiTile label="Nodes run" value={data.summary.nodes_run} accent="accent" />
          <KpiTile
            label="Errors"
            value={data.summary.error_count}
            accent={data.summary.error_count > 0 ? "danger" : "ok"}
          />
          <KpiTile
            label="Total duration"
            value={fmtTotalDuration(data.summary.total_duration_ms)}
          />
        </div>

        {#if data.summary.path_taken?.length}
          <div class="mt-4">
            <div class="text-xs uppercase text-muted">Path taken</div>
            <div class="mt-2 flex flex-wrap gap-1">
              {#each data.summary.path_taken as p}
                <span class="pill pill-accent text-xs">{p}</span>
              {/each}
            </div>
          </div>
        {/if}

        <div class="mt-4">
          <div class="text-xs uppercase text-muted">Timeline</div>
          <ol class="mt-2 space-y-2">
            {#each data.trace as entry, i}
              {@const hasError = entry.error != null && entry.error !== ""}
              <li class="rounded-lg border border-border bg-elevated p-3">
                <button
                  type="button"
                  class="flex w-full items-center gap-2 text-left"
                  onclick={() => (expandedRows = { ...expandedRows, [i]: !expandedRows[i] })}
                  aria-expanded={!!expandedRows[i]}
                >
                  {#if expandedRows[i]}
                    <ChevronDown size="14" class="text-muted" />
                  {:else}
                    <ChevronRight size="14" class="text-muted" />
                  {/if}
                  <span class="font-mono text-sm">{entryNode(entry)}</span>
                  <span class="ml-auto inline-flex items-center gap-1 text-xs text-muted">
                    <Clock size="12" />
                    {entryDuration(entry)}
                  </span>
                  {#if hasError}
                    <span class="inline-flex items-center gap-1 rounded bg-danger/15 px-2 py-0.5 text-xs text-danger">
                      <AlertCircle size="12" />
                      error
                    </span>
                  {/if}
                </button>
                {#if expandedRows[i]}
                  <div class="mt-2">
                    {#if hasError}
                      <p class="mb-2 text-xs text-danger">{entry.error}</p>
                    {/if}
                    <JsonView value={entry} />
                  </div>
                {/if}
              </li>
            {/each}
          </ol>
        </div>
      {/if}
    </div>
  {/if}
</div>
