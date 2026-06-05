<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { dashboardApi } from "$lib/api";
  import type { TxnRateSummary } from "$lib/api";
  import { fmtMyr } from "$lib/utils";
  import { Activity } from "lucide-svelte";

  interface Props {
    /** Minutes of history to display (default 60 — one bar per minute). */
    minutes?: number;
    /** Polling interval in ms. 0 disables polling. */
    refreshMs?: number;
  }
  let { minutes = 60, refreshMs = 15_000 }: Props = $props();

  let data = $state<TxnRateSummary | null>(null);
  let loading = $state(false);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function load() {
    loading = true;
    try {
      data = await dashboardApi.transactionRate(minutes);
    } catch {
      // swallow — empty state already renders.
    } finally {
      loading = false;
    }
  }

  // Pad sparse buckets so we always have `minutes` bars (gaps render as zeros).
  const bars = $derived.by(() => {
    const raw = data?.buckets ?? [];
    if (raw.length === 0) return [] as { ts: string; count: number; quarantined: number }[];
    const byMin = new Map<number, { count: number; quarantined: number }>();
    for (const b of raw) {
      const t = Date.parse(b.ts);
      if (!Number.isNaN(t)) {
        // Floor to the minute so duplicates merge cleanly.
        const k = Math.floor(t / 60_000) * 60_000;
        byMin.set(k, { count: b.count, quarantined: b.quarantined });
      }
    }
    const now = Math.floor(Date.now() / 60_000) * 60_000;
    const out: { ts: string; count: number; quarantined: number }[] = [];
    for (let i = minutes - 1; i >= 0; i--) {
      const k = now - i * 60_000;
      const v = byMin.get(k);
      out.push({
        ts: new Date(k).toISOString(),
        count: v?.count ?? 0,
        quarantined: v?.quarantined ?? 0
      });
    }
    return out;
  });

  const maxCount = $derived(Math.max(1, ...bars.map((b) => b.count)));
  const tps = $derived(
    bars.length > 0
      ? bars[bars.length - 1].count / 60  // counts in the most-recent minute / 60s
      : 0
  );

  onMount(() => {
    load();
    if (refreshMs > 0) {
      timer = setInterval(load, refreshMs);
    }
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
  });
</script>

<section class="card p-4">
  <header class="mb-3 flex items-start justify-between gap-3">
    <div class="flex items-center gap-2">
      <Activity size="14" class="text-accent" />
      <div>
        <h3 class="text-sm font-semibold">Transaction throughput</h3>
        <p class="text-[11px] text-muted">per-minute volume, last {minutes}m</p>
      </div>
    </div>
    <div class="text-right">
      <div class="text-xl font-semibold tabular-nums">
        {(data?.total_count ?? 0).toLocaleString()}
      </div>
      <div class="text-[11px] text-muted tabular-nums">
        {fmtMyr(data?.total_myr ?? 0)} · ~{tps.toFixed(1)} tps
      </div>
    </div>
  </header>

  {#if loading && bars.length === 0}
    <p class="text-xs text-muted">Loading…</p>
  {:else if bars.length === 0}
    <p class="text-xs text-muted">No transactions in the window.</p>
  {:else}
    <div class="flex items-end gap-[2px] h-20" aria-label="Per-minute transaction count">
      {#each bars as b}
        {@const f = b.count / maxCount}
        {@const qf = b.count > 0 ? b.quarantined / b.count : 0}
        <div
          class="flex-1 min-w-[2px] flex flex-col-reverse rounded-sm bg-elevated overflow-hidden"
          title={`${new Date(b.ts).toLocaleTimeString()} — ${b.count} txn (${b.quarantined} quarantined)`}
        >
          <div
            class="bg-accent/80"
            style={`height: ${Math.max(b.count > 0 ? 6 : 0, f * 100).toFixed(1)}%`}
          ></div>
          {#if qf > 0}
            <div
              class="bg-danger/90"
              style={`height: ${(qf * f * 100).toFixed(1)}%`}
            ></div>
          {/if}
        </div>
      {/each}
    </div>
    <div class="mt-2 flex items-center justify-between text-[10px] text-muted tabular-nums">
      <span>-{minutes}m</span>
      <span>now</span>
    </div>
  {/if}
</section>
