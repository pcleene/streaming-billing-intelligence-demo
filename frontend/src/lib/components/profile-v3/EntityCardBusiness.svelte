<script lang="ts">
  import type { EntityProfileBusiness } from "$lib/types";
  import { Building2 } from "lucide-svelte";
  import { fmtMyr } from "$lib/utils";
  interface Props { profile: EntityProfileBusiness }
  let { profile }: Props = $props();
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-accent/15 p-1.5 text-accent"><Building2 size="14" /></span>
    <div class="text-sm font-medium">Acme Business</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Outlets</dt><dd>{profile.outlet_count}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Package</dt><dd>{profile.primary_package}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">MRR</dt><dd>{fmtMyr(profile.monthly_mrr_myr)}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Member since</dt><dd>{profile.member_since}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Renewal</dt><dd>{profile.contract_renewal_at}</dd></div>
    {#if profile.ssm_number}
      <div class="flex justify-between"><dt class="text-muted">SSM</dt><dd class="font-mono text-xs">{profile.ssm_number}</dd></div>
    {/if}
  </dl>
</div>
