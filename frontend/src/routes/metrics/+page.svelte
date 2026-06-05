<script lang="ts">
  import { onMount } from "svelte";
  import { Activity, Zap, Clock, RefreshCw } from "lucide-svelte";
  import { metricsApi } from "$lib/api";
  import type { BurstStatus, BurstSample } from "$lib/types";
  import Section from "$lib/components/Section.svelte";
  import BurstSummaryStrip from "$lib/components/BurstSummaryStrip.svelte";
  import BurstSampleSparkline from "$lib/components/BurstSampleSparkline.svelte";

  // ---------------- state ----------------
  let runIdInput = $state("");
  let limit = $state<60 | 240 | 720>(240);
  let status = $state<BurstStatus | null>(null);
  let loading = $state(true);
  let err = $state<string | null>(null);

  const samples = $derived<BurstSample[]>(status?.samples ?? []);
  // Newest-first for the table (rows are already newest-first per backend).
  const tableRows = $derived<BurstSample[]>(
    status?.rows && status.rows.length ? status.rows : [...samples].reverse()
  );

  const tpsSeries = $derived(
    samples.map((s) => Number(s.observed_tps ?? 0))
  );
  const p99Series = $derived(
    samples.map((s) =>
      Number(s.rule_eval_p99_ms ?? s.p99_ms_ingest ?? 0)
    )
  );

  const hasEvalQueue = $derived(
    samples.some((s) => s.eval_queue_depth != null)
  );

  // ---------------- fetch ----------------
  async function refresh() {
    loading = true;
    err = null;
    try {
      const r = await metricsApi.burst({
        run_id: runIdInput.trim() || undefined,
        limit
      });
      status = r;
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
      status = null;
    } finally {
      loading = false;
    }
  }

  function onLimitChange(e: Event) {
    const v = Number((e.target as HTMLSelectElement).value);
    if (v === 60 || v === 240 || v === 720) {
      limit = v;
      refresh();
    }
  }

  function onRunIdSubmit(e: Event) {
    e.preventDefault();
    refresh();
  }

  function fmtTime(ts?: string | null): string {
    if (!ts) return "—";
    try {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return ts;
      return d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
    } catch {
      return ts;
    }
  }

  function fmtNum(n: unknown, digits = 0): string {
    const v = Number(n);
    if (!Number.isFinite(v)) return "—";
    return v.toFixed(digits);
  }

  onMount(refresh);
</script>

<div class="space-y-6">
  <header class="flex flex-wrap items-end justify-between gap-4">
    <div>
      <h1 class="flex items-center gap-2 text-2xl font-semibold">
        <Zap class="h-5 w-5 text-warn" /> Burst Mode Detail
      </h1>
      <p class="text-sm text-muted">
        Per-run breakdown of observed TPS, rule-evaluation p99, and breach
        counts. When no run id is provided we resolve the latest one.
      </p>
    </div>

    <form
      class="flex flex-wrap items-end gap-3"
      onsubmit={onRunIdSubmit}
    >
      <label class="flex flex-col text-xs text-muted">
        <span>run_id (optional)</span>
        <input
          type="text"
          bind:value={runIdInput}
          placeholder="leave empty for latest"
          class="mt-1 w-56 rounded border border-border bg-bg px-2 py-1 font-mono text-xs text-fg focus:border-accent focus:outline-none"
        />
      </label>

      <label class="flex flex-col text-xs text-muted">
        <span>limit</span>
        <select
          value={limit}
          onchange={onLimitChange}
          class="mt-1 rounded border border-border bg-bg px-2 py-1 text-xs text-fg focus:border-accent focus:outline-none"
        >
          <option value={60}>60</option>
          <option value={240}>240</option>
          <option value={720}>720</option>
        </select>
      </label>

      <button
        type="submit"
        class="flex items-center gap-1 rounded border border-border bg-bg px-3 py-1.5 text-xs hover:border-accent hover:text-accent disabled:opacity-50"
        disabled={loading}
        aria-label="Refresh"
      >
        <RefreshCw class="h-3.5 w-3.5 {loading ? 'animate-spin' : ''}" />
        Refresh
      </button>
    </form>
  </header>

  {#if err}
    <div class="card border-danger/40 p-4 text-sm text-danger">
      Failed to load burst metrics: {err}
    </div>
  {/if}

  {#if loading && !status}
    <div class="card p-6 text-sm text-muted">Loading burst run…</div>
  {:else}
    <div class="card flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-xs text-muted">
      <span>
        run id:
        <code class="text-fg">
          {status?.run_id ?? "—"}
        </code>
      </span>
      <span class="flex items-center gap-1">
        <Activity class="h-3.5 w-3.5" />
        state:
        <span class={status?.active ? "text-warn" : "text-ok"}>
          {status?.active ? "active" : "completed"}
        </span>
      </span>
      <span class="flex items-center gap-1">
        <Clock class="h-3.5 w-3.5" />
        started {fmtTime(status?.started_at)} · ended {fmtTime(status?.ended_at)}
      </span>
    </div>

    <BurstSummaryStrip
      summary={status?.summary ?? {}}
      active={Boolean(status?.active)}
    />

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <BurstSampleSparkline
        points={tpsSeries}
        label="Observed TPS"
        unit="tps"
        accent="text-accent"
      />
      <BurstSampleSparkline
        points={p99Series}
        label="Rule eval p99"
        unit="ms"
        accent="text-warn"
      />
    </div>

    <Section
      title="Samples"
      subtitle={`Newest first — showing ${tableRows.length} of up to ${limit}`}
    >
      {#if tableRows.length === 0}
        <div class="rounded border border-dashed border-border p-6 text-center text-sm text-muted">
          No burst samples yet. Once the metrics_recorder worker emits a
          burst window this view will populate automatically.
        </div>
      {:else}
        <div class="max-h-[480px] overflow-y-auto">
          <table class="w-full text-left text-xs">
            <thead class="sticky top-0 bg-bg/95 text-muted">
              <tr class="border-b border-border">
                <th class="px-2 py-2 font-medium">timestamp</th>
                <th class="px-2 py-2 font-medium text-right">tps</th>
                <th class="px-2 py-2 font-medium text-right">p99 ms</th>
                <th class="px-2 py-2 font-medium text-right">p50 ms</th>
                {#if hasEvalQueue}
                  <th class="px-2 py-2 font-medium text-right">eval queue</th>
                {/if}
                <th class="px-2 py-2 font-medium">mode</th>
              </tr>
            </thead>
            <tbody>
              {#each tableRows as s, i (s.recorded_at ?? i)}
                {@const p99 = s.rule_eval_p99_ms ?? s.p99_ms_ingest}
                <tr class="border-b border-border/60">
                  <td class="px-2 py-1.5 font-mono">{fmtTime(s.recorded_at)}</td>
                  <td class="px-2 py-1.5 text-right font-mono">
                    {fmtNum(s.observed_tps, 0)}
                  </td>
                  <td
                    class="px-2 py-1.5 text-right font-mono {Number(p99) > 200 ? 'text-danger' : ''}"
                  >
                    {fmtNum(p99, 0)}
                  </td>
                  <td class="px-2 py-1.5 text-right font-mono text-muted">
                    {fmtNum(s.p50_ms_ingest, 0)}
                  </td>
                  {#if hasEvalQueue}
                    <td class="px-2 py-1.5 text-right font-mono text-muted">
                      {s.eval_queue_depth != null ? fmtNum(s.eval_queue_depth, 0) : "—"}
                    </td>
                  {/if}
                  <td class="px-2 py-1.5">
                    <span
                      class={s.mode === "burst"
                        ? "text-warn"
                        : s.mode === "steady"
                          ? "text-ok"
                          : "text-muted"}
                    >
                      {s.mode ?? "—"}
                    </span>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </Section>
  {/if}
</div>
