<script lang="ts">
  import { onMount } from "svelte";
  import { polishApi } from "$lib/api";
  import type { CasesBySeveritySummary, SeverityCasesRow } from "$lib/api";
  import { ShieldAlert } from "lucide-svelte";

  interface Props { hours?: number }
  let { hours = 24 }: Props = $props();

  let data = $state<CasesBySeveritySummary | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      data = await polishApi.casesBySeverity(hours);
      error = null;
    } catch (e: unknown) {
      data = null;
      error = e instanceof Error ? e.message : "cases-by-severity unavailable";
    } finally {
      loading = false;
    }
  }

  // Canonical severity ordering for the bar chart.
  const SEVERITY_ORDER: Record<string, number> = {
    critical: 0,
    high: 1,
    medium: 2,
    low: 3
  };
  const sorted = $derived<SeverityCasesRow[]>(
    (data?.rows ?? [])
      .slice()
      .sort(
        (a, b) =>
          (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
      )
  );
  const maxRate = $derived(Math.max(1, ...sorted.map((r) => r.cases_per_day)));

  function tint(sev: string) {
    switch (sev) {
      case "critical": return "rgba(217, 70, 239, 0.9)";  // magenta
      case "high":     return "rgba(239, 68, 68, 0.85)";  // red
      case "medium":   return "rgba(245, 158, 11, 0.85)"; // amber
      case "low":      return "rgba(34, 197, 94, 0.8)";   // green
      default:         return "rgba(148, 163, 184, 0.7)"; // slate
    }
  }

  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-3 flex items-center justify-between">
    <div class="flex items-center gap-2">
      <ShieldAlert size="14" class="text-accent" />
      <h3 class="text-sm font-semibold">Cases by severity</h3>
    </div>
    <span class="text-[11px] text-muted">last {hours}h</span>
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading…</p>
  {:else if error}
    <p class="text-xs text-muted">{error}</p>
  {:else if sorted.length === 0}
    <p class="text-xs text-muted">No cases in the window.</p>
  {:else}
    <ul class="space-y-1.5">
      {#each sorted as r}
        {@const f = r.cases_per_day / maxRate}
        <li class="flex items-center gap-2 text-xs">
          <span class="w-20 truncate font-medium capitalize" title={r.severity}>
            {r.severity}
          </span>
          <div class="relative flex-1 h-3 rounded-full bg-elevated overflow-hidden">
            <div
              class="absolute inset-y-0 left-0"
              style={`width: ${Math.max(2, f * 100).toFixed(1)}%; background: ${tint(r.severity)}`}
            ></div>
          </div>
          <span class="w-12 text-right tabular-nums text-muted">
            {r.cases_count.toLocaleString()}
          </span>
          {#if r.open_cases != null}
            <span class="w-14 text-right tabular-nums text-[10px] text-muted">
              {r.open_cases} open
            </span>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>
