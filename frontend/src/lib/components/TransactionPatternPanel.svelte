<script lang="ts">
  import Section from "./Section.svelte";
  import { BarChart3 } from "lucide-svelte";
  import { customersRefreshApi } from "$lib/api";
  import type { TransactionPattern } from "$lib/types";
  import { fmtMyr, fmtDate, fmtRelative } from "$lib/utils";

  interface Props {
    customerId: string;
  }
  let { customerId }: Props = $props();

  const DAYS_OPTIONS: number[] = [7, 30, 90];
  let days = $state<number>(30);
  let pattern = $state<TransactionPattern | null>(null);
  let loading = $state(false);
  let err = $state<string | null>(null);

  async function load() {
    if (!customerId) return;
    loading = true;
    err = null;
    try {
      pattern = await customersRefreshApi.transactionPattern(customerId, days);
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
      pattern = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    void customerId;
    void days;
    load();
  });

  function pickDays(d: number) {
    days = d;
  }

  const topCodes = $derived((pattern?.top_charge_codes ?? []).slice(0, 5));
</script>

<Section
  title="Transaction pattern"
  subtitle={pattern ? `${pattern.txn_count} txns over the selected window` : "Aggregate stats over a sliding window"}
>
  <div class="mb-3 flex items-center gap-2">
    <BarChart3 class="h-4 w-4 text-muted" aria-hidden="true" />
    <span class="text-xs text-muted">Window:</span>
    {#each DAYS_OPTIONS as d}
      <button
        type="button"
        data-testid={`btn-days-${d}`}
        class={
          "rounded-md border px-2.5 py-1 text-xs " +
          (days === d
            ? "border-accent bg-accent/10 text-accent"
            : "border-border hover:bg-elevated")
        }
        onclick={() => pickDays(d)}
        disabled={loading}
      >
        {d}d
      </button>
    {/each}
    {#if loading}<span class="ml-2 text-xs text-muted">loading…</span>{/if}
    {#if err}<span class="ml-2 text-xs text-danger">{err}</span>{/if}
  </div>

  {#if pattern}
    {#if pattern.txn_count === 0}
      <p class="text-sm text-muted">No transactions in the last {days} days.</p>
    {:else}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm" data-testid="pattern-stats">
        <div class="rounded-md border border-border p-3">
          <div class="text-xs uppercase tracking-wide text-muted">Count</div>
          <div class="mt-1 text-xl font-semibold">{pattern.txn_count}</div>
        </div>
        <div class="rounded-md border border-border p-3">
          <div class="text-xs uppercase tracking-wide text-muted">Mean</div>
          <div class="mt-1 text-xl font-semibold">{fmtMyr(pattern.amount_mean_myr)}</div>
        </div>
        <div class="rounded-md border border-border p-3">
          <div class="text-xs uppercase tracking-wide text-muted">Std dev</div>
          <div class="mt-1 text-xl font-semibold">{fmtMyr(pattern.amount_stddev_myr)}</div>
        </div>
        <div class="rounded-md border border-border p-3">
          <div class="text-xs uppercase tracking-wide text-muted">Last txn</div>
          <div class="mt-1 text-sm">{fmtRelative(pattern.last_txn_at)}</div>
          {#if pattern.first_txn_at}
            <div class="mt-0.5 text-[11px] text-muted">first {fmtDate(pattern.first_txn_at)}</div>
          {/if}
        </div>
      </div>

      <div class="mt-4">
        <h3 class="mb-2 text-xs uppercase tracking-wide text-muted">Top charge codes</h3>
        {#if topCodes.length === 0}
          <p class="text-xs text-muted">No charge codes recorded.</p>
        {:else}
          <ul class="divide-y divide-border rounded-md border border-border" data-testid="charge-codes">
            {#each topCodes as cc}
              <li class="flex items-center justify-between px-3 py-1.5 text-xs">
                <span class="font-mono">{cc.charge_code}</span>
                <span class="text-muted">{cc.count}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
  {:else if !loading && !err}
    <p class="text-sm text-muted">No data.</p>
  {/if}
</Section>
