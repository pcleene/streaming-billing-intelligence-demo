<script lang="ts">
  import type { SupportInteraction } from "$lib/types";
  import { fmtRelative } from "$lib/utils";
  import { Smile, Meh, Frown, ChevronDown, ChevronRight } from "lucide-svelte";

  interface Props { interactions: SupportInteraction[] }
  let { interactions }: Props = $props();

  let categoryFilter = $state<string>("");
  let sentimentFilter = $state<string>("");
  let openId = $state<string | null>(null);

  const categories = $derived(
    Array.from(new Set(interactions.map((i) => i.category))).sort()
  );

  const filtered = $derived(
    interactions.filter((i) => {
      if (categoryFilter && i.category !== categoryFilter) return false;
      if (sentimentFilter && i.sentiment !== sentimentFilter) return false;
      return true;
    })
  );

  function sentimentIcon(s: string) {
    if (s === "positive") return { Icon: Smile, color: "text-ok" };
    if (s === "negative") return { Icon: Frown, color: "text-danger" };
    return { Icon: Meh, color: "text-muted" };
  }

  function resolutionClass(r: string): string {
    if (r === "resolved") return "pill pill-ok text-[10px]";
    if (r === "escalated") return "pill pill-danger text-[10px]";
    return "pill pill-warn text-[10px]";
  }
</script>

<section class="card p-5">
  <header class="mb-3 flex flex-wrap items-center justify-between gap-2">
    <div>
      <h2 class="text-lg font-semibold">Support interactions</h2>
      <p class="text-xs text-muted">{interactions.length} tickets · filterable</p>
    </div>
    <div class="flex gap-2">
      <select bind:value={categoryFilter} class="input py-1 text-xs">
        <option value="">All categories</option>
        {#each categories as c}<option value={c}>{c}</option>{/each}
      </select>
      <select bind:value={sentimentFilter} class="input py-1 text-xs">
        <option value="">All sentiment</option>
        <option value="positive">positive</option>
        <option value="neutral">neutral</option>
        <option value="negative">negative</option>
      </select>
    </div>
  </header>

  {#if filtered.length === 0}
    <p class="text-sm text-muted">No interactions match filter.</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each filtered as t}
        {@const { Icon, color } = sentimentIcon(t.sentiment)}
        {@const open = openId === t.ticket_id}
        <li class="py-2.5 text-sm">
          <button
            class="w-full text-left flex items-center gap-3"
            onclick={() => (openId = open ? null : t.ticket_id)}
          >
            {#if open}<ChevronDown size="14" class="text-muted" />{:else}<ChevronRight size="14" class="text-muted" />{/if}
            <span class={color}><Icon size="14" /></span>
            <span class="font-mono text-xs text-fg/80">{t.ticket_id}</span>
            <span class="text-muted text-xs">{fmtRelative(t.date)}</span>
            <span class="pill pill-muted text-[10px]">{t.category} / {t.subcategory}</span>
            <span class="ml-auto flex items-center gap-2">
              <span class="text-muted text-xs">{t.channel}</span>
              <span class={resolutionClass(t.resolution)}>{t.resolution}</span>
            </span>
          </button>
          {#if open}
            <div class="mt-2 ml-7 rounded-md border border-border bg-elevated/40 p-3 text-xs">
              <div class="mb-1 text-muted">
                Agent <span class="font-mono">{t.agent_id}</span> · {t.resolution_time_minutes} min to {t.resolution}
              </div>
              <p class="whitespace-pre-wrap text-fg/80">{t.notes}</p>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>
