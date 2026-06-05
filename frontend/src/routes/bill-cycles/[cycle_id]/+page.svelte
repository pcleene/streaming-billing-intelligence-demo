<script lang="ts">
  import { page } from "$app/state";
  import { billCyclesApi } from "$lib/api";
  import type { BillCycle } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { fmtMyr, fmtRelative } from "$lib/utils";

  const cycleId = $derived(page.params.cycle_id);
  let view = $state<BillCycle | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(false);

  async function load() {
    if (!cycleId) return;
    loading = true;
    error = null;
    try {
      view = await billCyclesApi.get(cycleId, { inspect: inspector.open });
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "load failed";
      view = null;
    } finally {
      loading = false;
    }
  }
  $effect(() => { load(); });
  $effect(() => { if (inspector.open) load(); });

  const variancePct = $derived(
    view && view.variance_myr != null && view.expected_amount_myr
      ? (view.variance_myr / view.expected_amount_myr) * 100
      : null
  );
  const varianceAccent = $derived(
    !view || view.variance_myr == null
      ? "default"
      : Math.abs(view.variance_myr) < 0.01
        ? "ok"
        : view.variance_myr > 0
          ? "warn"
          : "ok"
  );
</script>

<div class="space-y-6">
  <header>
    <div class="text-xs text-muted">Bill cycle</div>
    <div class="flex items-center gap-2">
      <h1 class="mt-1 text-2xl font-semibold font-mono">{cycleId}</h1>
      <InspectorTrigger hint="bill_cycles.find_one" />
    </div>
    {#if view}
      <p class="text-sm text-muted">
        Customer
        <a class="font-mono text-accent hover:underline" href={`/customers/${view.customer_id}`}>{view.customer_id}</a>
        · {view.cycle_start} → {view.cycle_end}
      </p>
    {/if}
  </header>

  {#if error}<div class="card p-3 text-sm text-danger">{error}</div>{/if}

  {#if view}
    <div class="grid-cards">
      <KpiTile label="Expected" value={fmtMyr(view.expected_amount_myr)} />
      <KpiTile label="Billed" value={view.billed_amount_myr == null ? "—" : fmtMyr(view.billed_amount_myr)} />
      <KpiTile
        label="Variance"
        value={view.variance_myr == null ? "—" : fmtMyr(view.variance_myr)}
        sub={variancePct == null ? "" : `${variancePct.toFixed(1)}% of expected`}
        accent={varianceAccent}
      />
      <KpiTile label="Cases" value={view.associated_quarantine_cases?.length ?? 0}
               accent={(view.associated_quarantine_cases?.length ?? 0) > 0 ? "warn" : "ok"} />
    </div>

    <Section title="Variance drivers" subtitle={`${view.variance_drivers?.length ?? 0} contributors`}>
      {#if (view.variance_drivers?.length ?? 0) === 0}
        <p class="text-sm text-muted">No variance drivers recorded.</p>
      {:else}
        <ul class="divide-y divide-border">
          {#each view.variance_drivers ?? [] as d}
            <li class="flex items-start justify-between gap-3 py-2 text-sm">
              <div>
                <div class="font-medium">{d.driver}</div>
                {#if d.note}<div class="text-xs text-muted">{d.note}</div>{/if}
              </div>
              <span class="text-right tabular-nums" class:text-warn={d.amount_myr > 0} class:text-ok={d.amount_myr < 0}>
                {fmtMyr(d.amount_myr)}
              </span>
            </li>
          {/each}
        </ul>
      {/if}
    </Section>

    {#if view.previous_cycle}
      <Section title="Previous cycle">
        <dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
          <div><dt class="text-muted text-xs">Cycle</dt><dd class="font-mono">{view.previous_cycle.cycle_id ?? "—"}</dd></div>
          <div><dt class="text-muted text-xs">Expected</dt><dd>{fmtMyr(view.previous_cycle.expected_amount_myr ?? 0)}</dd></div>
          <div><dt class="text-muted text-xs">Billed</dt><dd>{view.previous_cycle.billed_amount_myr == null ? "—" : fmtMyr(view.previous_cycle.billed_amount_myr)}</dd></div>
          <div><dt class="text-muted text-xs">Variance</dt><dd>{view.previous_cycle.variance_myr == null ? "—" : fmtMyr(view.previous_cycle.variance_myr)}</dd></div>
        </dl>
      </Section>
    {/if}

    <Section title="Associated quarantine cases" subtitle={`${view.associated_quarantine_cases?.length ?? 0}`}>
      {#if (view.associated_quarantine_cases?.length ?? 0) === 0}
        <p class="text-sm text-muted">No cases tied to this cycle.</p>
      {:else}
        <ul class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {#each view.associated_quarantine_cases ?? [] as c}
            <li>
              <a class="card block p-3 hover:bg-elevated/40" href={`/quarantine/${c.case_id}`}>
                <div class="flex items-center justify-between gap-2 text-sm">
                  <span class="font-mono text-accent">{c.case_id}</span>
                  {#if c.severity}<SeverityBadge severity={c.severity as 'low' | 'medium' | 'high'} />{/if}
                </div>
                <div class="mt-1 text-xs text-muted">
                  {c.rule_type ?? ""}{c.rule_type ? " · " : ""}
                  {c.status ?? ""}
                  {#if c.amount_myr != null} · {fmtMyr(c.amount_myr)}{/if}
                </div>
              </a>
            </li>
          {/each}
        </ul>
      {/if}
    </Section>

  {:else if loading}
    <div class="text-sm text-muted">Loading bill cycle…</div>
  {/if}
</div>
