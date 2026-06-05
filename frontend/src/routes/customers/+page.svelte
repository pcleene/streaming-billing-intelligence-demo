<script lang="ts">
  import { customersApi } from "$lib/api";
  import type { Customer360 } from "$lib/types";
  import Section from "$lib/components/Section.svelte";
  import { Sparkles } from "lucide-svelte";

  // Vector search rows have a different shape than the list rows
  // (subset of `Customer360`) — keep the table tolerant via `any` casts
  // rather than coercing both into a stricter type.
  type Row = (Customer360 & Partial<{ score: number; summary: string; state: string; entities: string[] }>) | Record<string, any>;
  let q = $state("");
  let items = $state<Row[]>([]);
  let loading = $state(false);
  let scoreMode = $state(false);

  let _debounce: ReturnType<typeof setTimeout> | null = null;

  async function runSearch(query: string): Promise<void> {
    loading = true;
    try {
      const trimmed = query.trim();
      if (trimmed.length === 0) {
        const r = await customersApi.list(0, 50);
        items = r.items as Row[];
        scoreMode = false;
      } else {
        const r = await customersApi.search(trimmed, 50);
        items = r.items as Row[];
        scoreMode = true;
      }
    } catch {
      items = [];
    } finally {
      loading = false;
    }
  }

  // Debounce the search-on-type so we don't spray Atlas Vector Search
  // requests on every keystroke (each call is a vector embed → ANN
  // lookup against the autoembed index).
  function onQueryChange(): void {
    if (_debounce) clearTimeout(_debounce);
    _debounce = setTimeout(() => runSearch(q), 350);
  }

  // Initial load.
  $effect(() => {
    if (items.length === 0) runSearch("");
  });
</script>

<div class="space-y-6">
  <header class="flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold">Customers</h1>
      <p class="text-sm text-muted">
        Atlas Vector Search over 10k Malaysian subscribers — natural language matches semantic profile signatures.
      </p>
    </div>
    <a class="btn inline-flex items-center gap-2" href="/customers/search" title="Faceted semantic search with score-explain panel">
      <Sparkles size="14" />
      Faceted search
    </a>
  </header>

  <Section title="Find a customer" subtitle="Try: “premium platinum customer in KL with high churn risk” or “cust_000123”">
    {#snippet actions()}
      <button class="btn btn-primary" onclick={() => runSearch(q)} disabled={loading}>
        {loading ? "Searching…" : "Search"}
      </button>
    {/snippet}
    <input
      class="input"
      placeholder="Search by semantic profile, name, ID, city…"
      bind:value={q}
      oninput={onQueryChange}
      onkeydown={(e) => { if (e.key === 'Enter') runSearch(q); }}
    />
  </Section>

  <Section title="Results" subtitle={`${items.length} shown${scoreMode ? ' · ranked by vector similarity' : ''}`}>
    {#if loading && items.length === 0}
      <p class="text-sm text-muted">Searching…</p>
    {:else if items.length === 0}
      <p class="text-sm text-muted">No matches.</p>
    {:else}
      <div class="overflow-hidden rounded-lg border border-border">
        <table class="w-full text-left text-sm">
          <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
            <tr>
              <th class="px-3 py-2">Customer</th>
              {#if scoreMode}
                <th class="px-3 py-2">Why relevant</th>
                <th class="px-3 py-2 text-right">Score</th>
              {:else}
                <th class="px-3 py-2">Segment</th>
                <th class="px-3 py-2">State</th>
                <th class="px-3 py-2">Email</th>
                <th class="px-3 py-2 text-right">Open cases</th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each items as c}
              <tr class="border-t border-border hover:bg-elevated/60">
                <td class="px-3 py-2">
                  <a class="font-mono text-accent hover:underline" href={`/customers/${c.customer_id}`}>
                    {c.customer_id}
                  </a>
                  <div class="text-xs text-muted">{c.name ?? "—"}</div>
                  {#if scoreMode && Array.isArray(c.entities) && c.entities.length > 0}
                    <div class="mt-1 flex flex-wrap gap-1">
                      {#each c.entities as e}
                        <span class="pill pill-muted text-[10px]">{e}</span>
                      {/each}
                    </div>
                  {/if}
                </td>
                {#if scoreMode}
                  <td class="px-3 py-2 text-xs text-fg/80 max-w-md truncate" title={c.summary ?? ""}>
                    {c.summary ?? "—"}
                  </td>
                  <td class="px-3 py-2 text-right tabular-nums text-xs text-muted">
                    {typeof c.score === "number" ? c.score.toFixed(3) : "—"}
                  </td>
                {:else}
                  <td class="px-3 py-2"><span class="pill pill-muted">{c.segment ?? "—"}</span></td>
                  <td class="px-3 py-2">{c.address?.state ?? c.state ?? "—"}</td>
                  <td class="px-3 py-2 text-muted">{c.email ?? "—"}</td>
                  <td class="px-3 py-2 text-right">{c.open_cases?.length ?? 0}</td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </Section>
</div>
