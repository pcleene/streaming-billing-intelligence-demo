<script lang="ts">
  import type { MarketingInteraction } from "$lib/types";
  import { fmtMyr } from "$lib/utils";

  interface Props { interactions: MarketingInteraction[] }
  let { interactions }: Props = $props();

  const stats = $derived.by(() => {
    const sent = interactions.length;
    const opened = interactions.filter((i) => i.opened_at).length;
    const clicked = interactions.filter((i) => i.clicked_at).length;
    const converted = interactions.filter((i) => i.converted_at).length;
    const revenue = interactions.reduce((a, i) => a + (i.revenue_attributed_myr ?? 0), 0);
    return { sent, opened, clicked, converted, revenue };
  });

  let hoverCampaign = $state<string | null>(null);

  const byCampaign = $derived.by(() => {
    const m = new Map<string, MarketingInteraction[]>();
    for (const i of interactions) {
      if (!m.has(i.campaign_id)) m.set(i.campaign_id, []);
      m.get(i.campaign_id)!.push(i);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  });

  function pct(num: number, denom: number): string {
    if (!denom) return "—";
    return `${((num / denom) * 100).toFixed(0)}%`;
  }
</script>

<section class="card p-5">
  <header class="mb-3 flex items-center justify-between">
    <div>
      <h2 class="text-lg font-semibold">Marketing funnel</h2>
      <p class="text-xs text-muted">Per-campaign attribution</p>
    </div>
    <div class="text-xs text-muted">Revenue <span class="text-ok font-semibold">{fmtMyr(stats.revenue)}</span></div>
  </header>

  <div class="grid grid-cols-4 gap-1.5">
    {#each [
      { label: "Sent", value: stats.sent, w: 100 },
      { label: "Opened", value: stats.opened, w: stats.sent ? (stats.opened / stats.sent) * 100 : 0 },
      { label: "Clicked", value: stats.clicked, w: stats.sent ? (stats.clicked / stats.sent) * 100 : 0 },
      { label: "Converted", value: stats.converted, w: stats.sent ? (stats.converted / stats.sent) * 100 : 0 }
    ] as step, idx}
      <div>
        <div class="text-[10px] uppercase tracking-wide text-muted">{step.label}</div>
        <div class="mt-1 text-lg font-semibold">{step.value}</div>
        <div class="mt-1 h-1.5 rounded-full bg-elevated">
          <div
            class="h-1.5 rounded-full"
            class:bg-accent={idx === 0}
            class:bg-accent2={idx === 1}
            class:bg-warn={idx === 2}
            class:bg-ok={idx === 3}
            style="width: {step.w}%"
          ></div>
        </div>
        {#if idx > 0}
          <div class="mt-1 text-[10px] text-muted">{pct(step.value, stats.sent)} of sent</div>
        {/if}
      </div>
    {/each}
  </div>

  {#if byCampaign.length > 0}
    <div class="mt-4">
      <div class="text-xs uppercase tracking-wide text-muted mb-2">By campaign</div>
      <ul class="space-y-1">
        {#each byCampaign as [cid, msgs]}
          {@const conv = msgs.filter((m) => m.converted_at).length}
          {@const rev = msgs.reduce((a, m) => a + (m.revenue_attributed_myr ?? 0), 0)}
          <li
            class="flex items-center justify-between rounded-md px-2 py-1.5 text-xs hover:bg-elevated/50"
            onmouseenter={() => (hoverCampaign = cid)}
            onmouseleave={() => (hoverCampaign = null)}
            role="listitem"
          >
            <span class="font-mono text-fg/80">{cid}</span>
            <span class="text-muted">{msgs.length} sent · {conv} converted · <span class="text-ok">{fmtMyr(rev)}</span></span>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</section>
