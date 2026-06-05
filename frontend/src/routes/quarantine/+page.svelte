<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { quarantineApi } from "$lib/api";
  import type { QuarantineCase } from "$lib/types";
  import { SseClient } from "$lib/sse";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import { fmtMyr, fmtRelative } from "$lib/utils";

  let status = $state<string>("open");
  let severity = $state<string>("");
  let rule_type = $state<string>("");
  let agentOnly = $state<boolean>(false);
  let total = $state(0);
  let items = $state<QuarantineCase[]>([]);
  let loading = $state(false);
  const sse = new SseClient();

  async function load(): Promise<void> {
    loading = true;
    try {
      const r = await quarantineApi.list({
        status,
        severity,
        rule_type,
        agent_reviewed: agentOnly || undefined,
        skip: 0,
        limit: 100
      });
      items = r.items;
      total = r.total;
    } finally {
      loading = false;
    }
  }
  $effect(() => { load(); });

  onMount(() => {
    sse.start();
    sse.on<QuarantineCase>("new_case", (c) => {
      items = [c, ...items].slice(0, 100);
      total += 1;
    });
  });
  onDestroy(() => sse.stop());
</script>

<div class="space-y-6">
  <header>
    <h1 class="text-2xl font-semibold">Quarantine queue</h1>
    <p class="text-sm text-muted">{total.toLocaleString()} cases match.</p>
  </header>

  <Section title="Filters">
    <div class="grid gap-3 sm:grid-cols-3">
      <label class="block text-sm">
        <span class="mb-1 block text-muted">Status</span>
        <select class="input" bind:value={status}>
          <option value="">all</option>
          <option value="open">open</option>
          <option value="under_review">under_review</option>
          <option value="resolved">resolved</option>
          <option value="dismissed">dismissed</option>
        </select>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block text-muted">Severity</span>
        <select class="input" bind:value={severity}>
          <option value="">all</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block text-muted">Rule type</span>
        <select class="input" bind:value={rule_type}>
          <option value="">all</option>
          <option value="discount_mismatch">discount_mismatch</option>
          <option value="velocity_anomaly">velocity_anomaly</option>
          <option value="amount_outlier">amount_outlier</option>
          <option value="entitlement_mismatch">entitlement_mismatch</option>
          <option value="geographic_anomaly">geographic_anomaly</option>
          <option value="duplicate_transaction">duplicate_transaction</option>
        </select>
      </label>
    </div>
    <div class="mt-3">
      <button
        type="button"
        class="pill {agentOnly ? 'pill-accent' : 'pill-muted'} cursor-pointer"
        aria-pressed={agentOnly}
        onclick={() => (agentOnly = !agentOnly)}
      >
        Agent-reviewed only
      </button>
    </div>
  </Section>

  <Section title="Cases" subtitle={loading ? "Loading…" : `${items.length} shown`}>
    {#if items.length === 0}
      <p class="text-sm text-muted">No cases.</p>
    {:else}
      <div class="overflow-hidden rounded-lg border border-border">
        <table class="w-full text-left text-sm">
          <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
            <tr>
              <th class="px-3 py-2">Severity</th>
              <th class="px-3 py-2">Case</th>
              <th class="px-3 py-2">Customer</th>
              <th class="px-3 py-2">Rules</th>
              <th class="px-3 py-2">Status</th>
              <th class="px-3 py-2 text-right">Amount</th>
              <th class="px-3 py-2 text-right">When</th>
            </tr>
          </thead>
          <tbody>
            {#each items as c}
              <tr class="border-t border-border hover:bg-elevated/60">
                <td class="px-3 py-2"><SeverityBadge severity={c.severity} /></td>
                <td class="px-3 py-2">
                  <a class="font-mono text-accent hover:underline" href={`/quarantine/${c.case_id}`}>{c.case_id}</a>
                </td>
                <td class="px-3 py-2">
                  <a class="font-mono text-fg/80 hover:underline" href={`/customers/${c.customer_id}`}>{c.customer_id}</a>
                </td>
                <td class="px-3 py-2 text-xs">
                  {#each c.rules_triggered ?? [] as r}
                    <span class="pill pill-accent mr-1">{r.rule_type}</span>
                  {/each}
                </td>
                <td class="px-3 py-2"><span class="pill pill-muted">{c.status}</span></td>
                <td class="px-3 py-2 text-right">{fmtMyr(c.amount)}</td>
                <td class="px-3 py-2 text-right text-muted">{fmtRelative(c.created_at)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </Section>
</div>
