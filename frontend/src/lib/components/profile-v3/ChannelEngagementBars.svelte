<script lang="ts">
  import type { ChannelEngagementRate } from "$lib/types";
  import { fmtRelative } from "$lib/utils";

  interface Props {
    rates: Partial<Record<string, ChannelEngagementRate>>;
  }
  let { rates }: Props = $props();

  const rows = $derived(
    Object.entries(rates)
      .filter(([, v]) => v != null)
      .map(([ch, v]) => ({ channel: ch, ...(v as ChannelEngagementRate) }))
      .sort((a, b) => (b.total_sent ?? 0) - (a.total_sent ?? 0))
  );
</script>

<section class="card p-5">
  <header class="mb-3">
    <h2 class="text-lg font-semibold">Channel engagement</h2>
    <p class="text-xs text-muted">Open · CTR · Conversion per channel</p>
  </header>

  {#if rows.length === 0}
    <p class="text-sm text-muted">No engagement data.</p>
  {:else}
    <ul class="space-y-3">
      {#each rows as r}
        <li>
          <div class="flex items-center justify-between text-sm">
            <span class="font-mono text-xs text-fg/90">{r.channel}</span>
            <span class="text-[11px] text-muted">{r.total_sent} sent · last {fmtRelative(r.last_engaged_at)}</span>
          </div>
          <div class="mt-1.5 flex h-2 overflow-hidden rounded-full bg-elevated">
            {#if r.open_rate > 0}
              <div
                class="bg-accent2/60"
                style="width: {Math.min(r.open_rate * 100, 100)}%"
                title="Open rate {(r.open_rate * 100).toFixed(1)}%"
              ></div>
            {/if}
            {#if r.ctr > 0}
              <div
                class="bg-warn/60"
                style="width: {Math.min(r.ctr * 100, 100)}%"
                title="CTR {(r.ctr * 100).toFixed(1)}%"
              ></div>
            {/if}
            {#if r.conversion_rate > 0}
              <div
                class="bg-ok/70"
                style="width: {Math.min(r.conversion_rate * 100, 100)}%"
                title="Conversion {(r.conversion_rate * 100).toFixed(1)}%"
              ></div>
            {/if}
          </div>
          <div class="mt-1 grid grid-cols-3 text-[11px] text-muted">
            <span>Open <span class="text-accent2">{(r.open_rate * 100).toFixed(1)}%</span></span>
            <span>CTR <span class="text-warn">{(r.ctr * 100).toFixed(1)}%</span></span>
            <span>Conv <span class="text-ok">{(r.conversion_rate * 100).toFixed(1)}%</span></span>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>
