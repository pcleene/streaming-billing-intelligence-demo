<script lang="ts">
  import type { EntityProfileCards } from "$lib/types";
  import { CreditCard } from "lucide-svelte";
  import { fmtRelative } from "$lib/utils";
  interface Props { profile: EntityProfileCards }
  let { profile }: Props = $props();
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-accent2/15 p-1.5 text-accent2"><CreditCard size="14" /></span>
    <div class="text-sm font-medium">Acme Cards</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Tier</dt><dd>{profile.tier}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Points</dt><dd>{profile.points_balance.toLocaleString()}</dd></div>
    {#if profile.last_redeemed_at}
      <div class="flex justify-between"><dt class="text-muted">Last redeem</dt><dd class="text-muted">{fmtRelative(profile.last_redeemed_at)}</dd></div>
    {/if}
  </dl>
</div>
