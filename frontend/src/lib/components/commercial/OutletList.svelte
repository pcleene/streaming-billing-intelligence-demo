<script lang="ts">
  import type { CustomerV3 } from "$lib/types";
  import { fmtMyr } from "$lib/utils";

  interface Props {
    outlets: CustomerV3[];
    page?: number;
    pageSize?: number;
  }
  let { outlets, page = $bindable(1), pageSize = 20 }: Props = $props();

  const total = $derived(outlets.length);
  const pages = $derived(Math.max(1, Math.ceil(total / pageSize)));
  const slice = $derived(outlets.slice((page - 1) * pageSize, page * pageSize));

  function revenueOf(o: CustomerV3): number {
    const t = o.cross_entity_metrics?.monthly_spend_trend_12m;
    if (t && t.length > 0) return t[t.length - 1].value;
    return o.current_cycle?.billed_amount_myr ?? o.current_cycle?.expected_amount_myr ?? 0;
  }
  function ppvOf(o: CustomerV3): number {
    return o.entity_profiles?.acme_streaming?.ppv_count_30d ?? 0;
  }
</script>

<section class="card p-5">
  <header class="mb-3 flex items-center justify-between">
    <div>
      <h2 class="text-lg font-semibold">Outlets</h2>
      <p class="text-xs text-muted">{total} total · page {page} of {pages}</p>
    </div>
    <div class="flex gap-1">
      <button class="btn text-xs" disabled={page <= 1} onclick={() => (page = Math.max(1, page - 1))}>Prev</button>
      <button class="btn text-xs" disabled={page >= pages} onclick={() => (page = Math.min(pages, page + 1))}>Next</button>
    </div>
  </header>

  {#if slice.length === 0}
    <p class="text-sm text-muted">No outlets in this account.</p>
  {:else}
    <div class="overflow-hidden rounded-lg border border-border">
      <table class="w-full text-left text-xs">
        <thead class="bg-elevated text-muted">
          <tr>
            <th class="px-3 py-2">Outlet</th>
            <th class="px-3 py-2">City</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2 text-right">PPV (30d)</th>
            <th class="px-3 py-2 text-right">Last-month revenue</th>
          </tr>
        </thead>
        <tbody>
          {#each slice as o}
            <tr class="border-t border-border">
              <td class="px-3 py-1.5">
                <a class="font-mono text-accent hover:underline" href={`/customers/commercial/${o.customer_id}`}>
                  {o.unified_profile?.name ?? o.customer_id}
                </a>
              </td>
              <td class="px-3 py-1.5">{o.unified_profile?.address?.city ?? "—"}</td>
              <td class="px-3 py-1.5 text-muted">{o.unified_profile?.address?.state ?? "—"}</td>
              <td class="px-3 py-1.5 text-right">{ppvOf(o)}</td>
              <td class="px-3 py-1.5 text-right">{fmtMyr(revenueOf(o))}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>
