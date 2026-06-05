<script lang="ts">
  import { customersV3Api } from "$lib/api";
  import type { CustomerSearchHit } from "$lib/api";
  import type { EntityKey } from "$lib/types";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { Search, Sparkles, ArrowRight } from "lucide-svelte";

  let q = $state("");
  let limit = $state(20);
  let customerType = $state<"" | "residential" | "commercial">("");
  let tier = $state("");
  let entity = $state<"" | EntityKey>("");
  let stateFilter = $state("");

  let hits = $state<CustomerSearchHit[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let lastQuery = $state<string | null>(null);

  const exampleChips: { label: string; q: string }[] = [
    {
      label: "Platinum customer with strong PPV velocity in Selangor",
      q: "platinum customer with strong PPV velocity in Selangor"
    },
    {
      label: "Commercial outlet, EPL entitlement, end-of-cycle high spend",
      q: "commercial outlet EPL entitlement end of cycle high spend"
    },
    {
      label: "Residential broadband subscriber with churn risk and support tickets",
      q: "residential broadband subscriber churn risk support tickets"
    }
  ];

  async function run() {
    const text = q.trim();
    if (!text) {
      hits = [];
      error = null;
      lastQuery = null;
      return;
    }
    loading = true;
    error = null;
    try {
      // Always send inspect=true on this page so the inspector chip can
      // open the popup with the executed $vectorSearch pipeline — the
      // canonical demo of the AutoEmbed semantic-search path.
      const r = await customersV3Api.search(
        text,
        {
          limit,
          customer_type: customerType || undefined,
          tier: tier || undefined,
          entity: entity || undefined,
          state: stateFilter || undefined
        },
        { inspect: true }
      );
      hits = r.items ?? [];
      lastQuery = text;
    } catch (e: unknown) {
      hits = [];
      error = e instanceof Error ? e.message : "search failed";
    } finally {
      loading = false;
    }
  }

  function pickExample(text: string) {
    q = text;
    run();
  }

  function handleKey(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      run();
    }
  }

  function tierTint(t?: string) {
    switch (t) {
      case "platinum": return "pill-accent";
      case "gold": return "pill-warn";
      case "silver": return "pill-muted";
      default: return "pill-muted";
    }
  }
</script>

<div class="space-y-6">
  <header class="flex flex-wrap items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-2">
        <h1 class="text-2xl font-semibold">Find customers like…</h1>
        <InspectorTrigger hint="customers.$vectorSearch" />
      </div>
      <p class="text-sm text-muted">
        AutoEmbed semantic search over the customer corpus. Voyage-4-large embeddings,
        Atlas <span class="font-mono">$vectorSearch</span> with metadata filters.
      </p>
    </div>
  </header>

  <section class="card p-4 space-y-3">
    <label class="block">
      <span class="mb-1 block text-xs uppercase tracking-wide text-muted">Query</span>
      <div class="flex items-center gap-2">
        <div class="relative flex-1">
          <Search size="14" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            class="input pl-8 w-full"
            placeholder="Describe a customer profile…"
            bind:value={q}
            onkeydown={handleKey}
          />
        </div>
        <button class="btn btn-primary inline-flex items-center gap-1" onclick={run} disabled={loading}>
          {#if loading}Searching…{:else}<Sparkles size="14" /> Search{/if}
        </button>
      </div>
    </label>

    <div class="flex flex-wrap items-center gap-2">
      <span class="text-[11px] uppercase tracking-wide text-muted">Try</span>
      {#each exampleChips as c}
        <button
          type="button"
          class="pill pill-muted text-[11px] hover:opacity-80"
          onclick={() => pickExample(c.q)}
        >
          {c.label}
        </button>
      {/each}
    </div>

    <div class="grid grid-cols-2 md:grid-cols-5 gap-2 pt-2">
      <label class="block text-xs">
        <span class="mb-1 block text-muted">Type</span>
        <select class="input text-xs" bind:value={customerType}>
          <option value="">Any</option>
          <option value="residential">Residential</option>
          <option value="commercial">Commercial</option>
        </select>
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-muted">Tier</span>
        <select class="input text-xs" bind:value={tier}>
          <option value="">Any</option>
          <option value="bronze">Bronze</option>
          <option value="silver">Silver</option>
          <option value="gold">Gold</option>
          <option value="platinum">Platinum</option>
        </select>
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-muted">Entity</span>
        <select class="input text-xs" bind:value={entity}>
          <option value="">Any</option>
          <option value="acme_paytv">Pay TV</option>
          <option value="acme_streaming">Streaming</option>
          <option value="acme_broadband">Broadband</option>
          <option value="acme_prepaid">PREPAID</option>
          <option value="acme_business">Business</option>
          <option value="acme_cards">Cards</option>
        </select>
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-muted">State</span>
        <input class="input text-xs" bind:value={stateFilter} placeholder="e.g. Selangor" />
      </label>
      <label class="block text-xs">
        <span class="mb-1 block text-muted">Limit</span>
        <input class="input text-xs" type="number" min="1" max="50" bind:value={limit} />
      </label>
    </div>
  </section>

  {#if error}
    <div class="card p-3 text-sm text-danger">
      {error}
      <p class="mt-1 text-xs text-muted">The search endpoint may not be served yet. Open the inspector to inspect the request.</p>
    </div>
  {/if}

  {#if loading}
    <p class="text-xs text-muted">Embedding query and running $vectorSearch…</p>
  {:else if hits.length === 0 && lastQuery !== null && !error}
    <p class="text-xs text-muted">No matches for "{lastQuery}". Try a broader phrasing.</p>
  {:else if hits.length > 0}
    <section class="space-y-2">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-[11px] uppercase tracking-wide text-muted">
          {hits.length} match{hits.length === 1 ? "" : "es"} · ranked by vectorSearchScore
        </p>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11px] font-mono text-accent hover:bg-accent/20 transition-colors"
          onclick={() => inspector.openPanel()}
          title="Inspect the executed $vectorSearch pipeline"
        >
          <Sparkles size="11" /> Show $vectorSearch
        </button>
      </div>
      <ul class="space-y-2">
        {#each hits as h}
          {@const href = h.customer_type === "commercial"
            ? `/customers/commercial/${h.customer_id}`
            : `/customers/residential/${h.customer_id}`}
          <li class="card p-3 hover:bg-elevated/60 transition-colors">
            <a class="block" href={href}>
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-semibold">{h.name || h.customer_id}</span>
                <span class="font-mono text-[11px] text-muted">{h.customer_id}</span>
                {#if h.tier}
                  <span class={`pill ${tierTint(h.tier)} uppercase text-[10px]`}>{h.tier}</span>
                {/if}
                <span class="pill pill-muted text-[10px]">{h.customer_type}</span>
                {#if h.state}<span class="pill pill-muted text-[10px]">{h.state}</span>{/if}
                <span class="ml-auto inline-flex items-center gap-2 text-xs text-muted">
                  <span title="vectorSearchScore">score {h.score.toFixed(3)}</span>
                  <ArrowRight size="12" />
                </span>
              </div>
              {#if h.entities && h.entities.length > 0}
                <div class="mt-2 flex flex-wrap gap-1">
                  {#each h.entities as e}
                    <span class="pill pill-accent text-[10px]">{e.replace("acme_", "")}</span>
                  {/each}
                </div>
              {/if}
              {#if h.summary}
                <p class="mt-2 text-sm text-muted line-clamp-2">{h.summary}</p>
              {/if}
            </a>
          </li>
        {/each}
      </ul>
    </section>
  {:else}
    <p class="text-xs text-muted">
      Enter a natural-language description of the customer you're looking for, or pick an example chip above.
    </p>
  {/if}
</div>
