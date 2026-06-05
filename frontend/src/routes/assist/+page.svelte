<script lang="ts">
  import { onMount } from "svelte";
  import { quarantineApi } from "$lib/api";
  import type { QuarantineCase } from "$lib/types";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import { fmtMyr, fmtRelative } from "$lib/utils";

  let queue = $state<QuarantineCase[]>([]);
  let loading = $state(false);

  async function load() {
    loading = true;
    try {
      const r = await quarantineApi.list({ status: "open", severity: "high", limit: 50 });
      queue = r.items;
    } finally {
      loading = false;
    }
  }
  onMount(load);
</script>

<div class="space-y-6">
  <header>
    <h1 class="text-2xl font-semibold">Analyst assist queue</h1>
    <p class="text-sm text-muted">High-severity open cases prioritised for AI-assisted triage.</p>
  </header>

  <Section title="Open · high-severity">
    {#snippet actions()}<button class="btn" onclick={load} disabled={loading}>{loading ? "…" : "Refresh"}</button>{/snippet}
    {#if queue.length === 0}
      <p class="text-sm text-muted">Queue empty.</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each queue as c}
          <li class="flex items-center gap-3 py-2 text-sm">
            <SeverityBadge severity={c.severity} />
            <a class="font-mono text-accent hover:underline" href={`/quarantine/${c.case_id}`}>{c.case_id}</a>
            <span class="text-muted">{c.customer_id}</span>
            <span class="text-xs text-muted ml-auto">{fmtRelative(c.created_at)}</span>
            <span class="w-24 text-right">{fmtMyr(c.amount)}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </Section>
</div>
