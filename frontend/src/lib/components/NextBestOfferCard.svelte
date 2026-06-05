<script lang="ts">
  import Section from "./Section.svelte";
  import { customersApi } from "$lib/api";
  import type { CustomerRecommendations, ChurnRiskBand } from "$lib/types";
  import { fmtMyr, fmtRelative } from "$lib/utils";

  interface Props {
    customerId: string;
    initial?: CustomerRecommendations | null;
  }
  let { customerId, initial = null }: Props = $props();

  let recs = $state<CustomerRecommendations | null>(null);
  let loading = $state(false);
  let err = $state<string | null>(null);

  $effect(() => {
    recs = initial ?? null;
  });

  async function refresh() {
    loading = true;
    err = null;
    try {
      recs = await customersApi.recommendations(customerId);
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function bandClass(b: ChurnRiskBand): string {
    return b === "high" ? "text-danger" : b === "medium" ? "text-warn" : "text-ok";
  }
  function bandLabel(b: ChurnRiskBand): string {
    return b.charAt(0).toUpperCase() + b.slice(1);
  }
</script>

<Section
  title="Next best offers"
  subtitle={recs ? `Computed ${fmtRelative(recs.computed_at)}` : "Heuristic projection from embedded fields"}
>
  <div class="mb-3 flex items-center gap-3">
    <button
      class="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-elevated disabled:opacity-50"
      onclick={refresh}
      disabled={loading}
    >
      {recs ? "Recompute" : "Compute now"}
    </button>
    {#if loading}<span class="text-xs text-muted">working…</span>{/if}
    {#if err}<span class="text-xs text-danger">{err}</span>{/if}
  </div>

  {#if recs}
    <div class="mb-4 rounded-md border border-border p-3">
      <div class="flex items-center justify-between text-sm">
        <span class="text-muted">Churn risk</span>
        <span class="{bandClass(recs.churn_risk.band)} font-semibold">
          {bandLabel(recs.churn_risk.band)} · {(recs.churn_risk.score * 100).toFixed(0)}%
        </span>
      </div>
      {#if recs.churn_risk.drivers.length}
        <ul class="mt-2 flex flex-wrap gap-1.5">
          {#each recs.churn_risk.drivers as d}
            <li class="rounded bg-elevated px-2 py-0.5 font-mono text-[11px] text-muted">
              {d}
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    {#if recs.next_best_offers.length === 0}
      <p class="text-sm text-muted">No offers triggered for this customer profile.</p>
    {:else}
      <ul class="space-y-2">
        {#each recs.next_best_offers as o}
          <li class="rounded-md border border-border p-3">
            <div class="flex items-start justify-between gap-3">
              <div>
                <div class="text-sm font-medium">{o.title}</div>
                <div class="mt-0.5 font-mono text-[11px] text-muted">
                  #{o.priority} · {o.offer_type} · {o.offer_id}
                </div>
              </div>
              <div class="text-right text-xs">
                <div class="text-muted">Expected</div>
                <div class="text-ok">{fmtMyr(o.expected_uplift_myr)}</div>
              </div>
            </div>
            <p class="mt-2 text-xs text-fg/80">{o.rationale}</p>
          </li>
        {/each}
      </ul>
    {/if}
  {:else if !loading}
    <p class="text-sm text-muted">
      Click <em>Compute now</em> to score churn risk and surface offers.
    </p>
  {/if}
</Section>
