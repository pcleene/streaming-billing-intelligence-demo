<script lang="ts">
  import { beforeAfterApi } from "$lib/api";
  import type { BeforeAfter, BeforeAfterProjection } from "$lib/types";
  import { fmtRelative } from "$lib/utils";

  interface Props {
    caseId: string;
  }
  let { caseId }: Props = $props();

  let data = $state<BeforeAfter | null>(null);
  let loading = $state(true);
  let err = $state<string | null>(null);

  async function load() {
    loading = true;
    err = null;
    try {
      data = await beforeAfterApi.get(caseId);
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
      data = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (caseId) load();
  });

  function sourceLabel(s: BeforeAfterProjection["source"]): string {
    return s === "crm_snapshot"
      ? "Warehouse snapshot"
      : s === "derived_lag"
        ? "Derived stale view"
        : "Live operational doc";
  }

  function fmtLatency(seconds: number | null): string {
    if (seconds == null) return "—";
    if (seconds < 60) return `${seconds.toFixed(1)} s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
    return `${(seconds / 3600).toFixed(1)} h`;
  }
</script>

<div class="card p-4">
  <div class="mb-3 flex items-center justify-between">
    <div>
      <h3 class="text-sm font-semibold uppercase tracking-wide text-muted">
        Mongo stream vs warehouse batch
      </h3>
      <p class="text-xs text-muted">
        What the analyst would have seen in a 24h-stale warehouse next to what
        the live platform shows now.
      </p>
    </div>
    <button
      class="rounded-md border border-border px-3 py-1 text-xs hover:bg-elevated"
      onclick={load}
      disabled={loading}
    >
      Refresh
    </button>
  </div>

  {#if loading}
    <div class="text-sm text-muted">Loading…</div>
  {:else if err}
    <div class="text-sm text-danger">{err}</div>
  {:else if data}
    <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
      <!-- Before column — greyed -->
      <div class="rounded-md border border-border p-3 opacity-60">
        <div class="mb-2 flex items-center justify-between">
          <span class="text-xs font-semibold uppercase text-muted">Before</span>
          <span class="text-[11px] text-muted">{sourceLabel(data.before.source)}</span>
        </div>
        <div class="text-[11px] text-muted">
          {data.before.snapshot_at ? fmtRelative(data.before.snapshot_at) : "—"}
        </div>
        <dl class="mt-2 space-y-1 text-sm">
          <div class="flex justify-between"><dt class="text-muted">Open cases</dt><dd>{data.before.open_case_count}</dd></div>
          <div class="flex justify-between"><dt class="text-muted">Recent txns</dt><dd>{data.before.recent_transaction_count}</dd></div>
          <div class="flex justify-between"><dt class="text-muted">AI assist</dt><dd>{data.before.has_ai_assist ? "yes" : "no"}</dd></div>
        </dl>
        {#if data.before.notes.length}
          <ul class="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted">
            {#each data.before.notes as n}<li>{n}</li>{/each}
          </ul>
        {/if}
      </div>

      <!-- After column — highlighted -->
      <div class="rounded-md border border-accent/40 bg-accent/5 p-3">
        <div class="mb-2 flex items-center justify-between">
          <span class="text-xs font-semibold uppercase text-accent">After</span>
          <span class="text-[11px] text-muted">{sourceLabel(data.after.source)}</span>
        </div>
        <div class="text-[11px] text-muted">
          {data.after.snapshot_at ? fmtRelative(data.after.snapshot_at) : "now"}
        </div>
        <dl class="mt-2 space-y-1 text-sm">
          <div class="flex justify-between"><dt class="text-muted">Open cases</dt><dd>{data.after.open_case_count}</dd></div>
          <div class="flex justify-between"><dt class="text-muted">Recent txns</dt><dd>{data.after.recent_transaction_count}</dd></div>
          <div class="flex justify-between">
            <dt class="text-muted">AI assist</dt>
            <dd>{data.after.has_ai_assist ? "yes" : "no"}</dd>
          </div>
        </dl>
        {#if data.after.rules_visible.length}
          <div class="mt-2 flex flex-wrap gap-1">
            {#each data.after.rules_visible as r}
              <span class="rounded bg-elevated px-2 py-0.5 font-mono text-[10px]">{r}</span>
            {/each}
          </div>
        {/if}
        {#if data.after.ai_assist_summary}
          <p class="mt-2 text-[11px] text-fg/80">{data.after.ai_assist_summary}</p>
        {/if}
      </div>
    </div>

    <div class="mt-3 grid grid-cols-2 gap-3 text-xs">
      <div class="rounded-md bg-elevated p-2">
        <div class="text-muted">Would warehouse have caught it?</div>
        <div class="mt-0.5 font-semibold {data.would_quarantine ? 'text-ok' : 'text-warn'}">
          {data.would_quarantine ? "Yes — txn predates snapshot" : "No — case missed"}
        </div>
      </div>
      <div class="rounded-md bg-elevated p-2">
        <div class="text-muted">Auto-resolution latency</div>
        <div class="mt-0.5 font-semibold">{fmtLatency(data.auto_resolution_latency_seconds)}</div>
      </div>
    </div>
  {/if}
</div>
