<script lang="ts">
  import { page } from "$app/state";
  import { customersV3Api } from "$lib/api";
  import type { CustomerV3 } from "$lib/types";
  import OutletList from "$lib/components/commercial/OutletList.svelte";
  import OutletRevenueDistribution from "$lib/components/commercial/OutletRevenueDistribution.svelte";
  import { ArrowLeft } from "lucide-svelte";

  const parentId = $derived(page.params.parent_id);
  let outlets = $state<CustomerV3[]>([]);
  let error = $state<string | null>(null);
  let loading = $state(false);
  let pg = $state(1);

  async function load() {
    if (!parentId) return;
    loading = true;
    error = null;
    try {
      const res = await customersV3Api.outlets(parentId, 0, 200);
      outlets = res.items ?? [];
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "outlets unavailable";
      outlets = [];
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-6">
  <a class="inline-flex items-center gap-1 text-xs text-accent hover:underline" href={`/customers/commercial/${parentId}`}>
    <ArrowLeft size="12" /> Back to parent account
  </a>

  <header>
    <h1 class="text-2xl font-semibold">Outlets</h1>
    <p class="text-sm text-muted">Parent <span class="font-mono">{parentId}</span> · {outlets.length} outlets</p>
  </header>

  {#if error}<div class="card p-3 text-sm text-danger">{error}</div>{/if}

  {#if outlets.length > 0 && parentId}
    <OutletRevenueDistribution outlets={outlets} parentId={parentId} />
    <OutletList outlets={outlets} bind:page={pg} pageSize={20} />
  {:else if loading}
    <div class="text-sm text-muted">Loading outlets…</div>
  {:else}
    <div class="card p-4 text-sm text-muted">No outlets returned for this parent.</div>
  {/if}
</div>
