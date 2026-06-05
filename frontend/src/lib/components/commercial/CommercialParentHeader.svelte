<script lang="ts">
  import type { CustomerV3 } from "$lib/types";
  import { Building2, CalendarDays } from "lucide-svelte";
  import { fmtMyr } from "$lib/utils";

  interface Props { customer: CustomerV3 }
  let { customer }: Props = $props();

  const biz = $derived(customer.entity_profiles?.acme_business);
  // Flat-root V3 (PR-15) lifted identity scalars to the document root;
  // the deprecated `unified_profile` block is kept as a fallback for
  // cached pre-PR-15 responses.
  const displayName = $derived(customer.name ?? customer.unified_profile?.name ?? "—");
  const address = $derived(customer.address ?? customer.unified_profile?.address ?? null);
</script>

<section class="card-elevated p-5">
  <div class="flex items-start justify-between gap-4">
    <div>
      <div class="flex items-center gap-2 text-xs text-muted">
        <Building2 size="14" />
        Commercial parent account
        <span class="font-mono">{customer.account_id}</span>
      </div>
      <h1 class="mt-1 text-2xl font-semibold">{displayName}</h1>
      <div class="mt-1 text-sm text-muted">
        {address?.city ?? ""}{address?.city ? ", " : ""}
        {address?.state ?? ""}
        {#if biz?.ssm_number} · SSM <span class="font-mono">{biz.ssm_number}</span>{/if}
      </div>
    </div>
    <div class="grid grid-cols-3 gap-4 text-right">
      <div>
        <div class="text-xs text-muted">Outlets</div>
        <div class="text-xl font-semibold">{biz?.outlet_count ?? "—"}</div>
      </div>
      <div>
        <div class="text-xs text-muted">MRR</div>
        <div class="text-xl font-semibold">{biz ? fmtMyr(biz.monthly_mrr_myr) : "—"}</div>
      </div>
      <div>
        <div class="text-xs text-muted flex items-center gap-1 justify-end"><CalendarDays size="11" /> Renewal</div>
        <div class="text-sm font-mono">{biz?.contract_renewal_at ?? "—"}</div>
      </div>
    </div>
  </div>
</section>
