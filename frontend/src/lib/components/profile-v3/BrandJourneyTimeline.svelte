<script lang="ts">
  import type { BrandJourneyEvent } from "$lib/types";
  import { Tv, MonitorPlay, Wifi, Radio, Building2, CreditCard, Circle } from "lucide-svelte";

  interface Props { events: BrandJourneyEvent[] }
  let { events }: Props = $props();

  // lucide-svelte icons are typed as `ComponentType` but we just need them
  // assignable to `<Icon />` — keep the record loose to avoid friction with
  // the Svelte 5 `Component` brand.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const iconFor: Record<string, any> = {
    acme_paytv: Tv,
    acme_streaming: MonitorPlay,
    acme_broadband: Wifi,
    acme_prepaid: Radio,
    acme_business: Building2,
    acme_cards: CreditCard
  };

  function quarterOf(date: string): string {
    const d = new Date(date);
    if (isNaN(d.getTime())) return "—";
    return `${d.getFullYear()} Q${Math.floor(d.getMonth() / 3) + 1}`;
  }

  const groups = $derived.by(() => {
    const map = new Map<string, BrandJourneyEvent[]>();
    for (const ev of [...events].sort((a, b) => a.date.localeCompare(b.date))) {
      const q = quarterOf(ev.date);
      if (!map.has(q)) map.set(q, []);
      map.get(q)!.push(ev);
    }
    return [...map.entries()];
  });

  let openIdx = $state<string | null>(null);
</script>

<section class="card p-5">
  <header class="mb-4">
    <h2 class="text-lg font-semibold">Brand journey</h2>
    <p class="text-xs text-muted">First touch through every entity, grouped by quarter</p>
  </header>

  {#if events.length === 0}
    <p class="text-sm text-muted">No journey events recorded.</p>
  {:else}
    <ol class="relative space-y-4 border-l border-border pl-5">
      {#each groups as [quarter, items]}
        <li>
          <div class="-ml-7 mb-2 inline-flex items-center gap-2 rounded-full border border-border bg-elevated px-2 py-0.5 text-[10px] font-mono text-muted">
            {quarter}
          </div>
          <ul class="space-y-2">
            {#each items as ev, i}
              {@const Icon = iconFor[ev.entity as string] ?? Circle}
              {@const key = `${quarter}:${i}`}
              <li class="relative">
                <span class="absolute -left-[26px] top-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full border border-border bg-surface text-accent">
                  <Icon size="10" />
                </span>
                <button
                  class="text-left w-full rounded-md px-2 py-1.5 text-sm hover:bg-elevated/50"
                  onclick={() => (openIdx = openIdx === key ? null : key)}
                >
                  <div class="flex items-center justify-between">
                    <span><span class="text-muted text-xs font-mono">{ev.entity}</span> · {ev.event}</span>
                    <span class="text-xs text-muted">{ev.date}</span>
                  </div>
                </button>
                {#if openIdx === key && ev.details}
                  <pre class="ml-2 mt-1 max-h-40 overflow-auto rounded-md border border-border bg-elevated/70 p-2 text-[11px] text-muted">{JSON.stringify(ev.details, null, 2)}</pre>
                {/if}
              </li>
            {/each}
          </ul>
        </li>
      {/each}
    </ol>
  {/if}
</section>
