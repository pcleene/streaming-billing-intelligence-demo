<script lang="ts">
  import type { EntityProfilePayTv } from "$lib/types";
  import { Tv } from "lucide-svelte";
  import { fmtMyr } from "$lib/utils";
  interface Props { profile: EntityProfilePayTv }
  let { profile }: Props = $props();
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-accent/15 p-1.5 text-accent"><Tv size="14" /></span>
    <div class="text-sm font-medium">Acme Pay TV</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Package</dt><dd>{profile.primary_package}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">MRR</dt><dd>{fmtMyr(profile.monthly_mrr_myr)}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Member since</dt><dd>{profile.member_since}</dd></div>
    {#if profile.household_size != null}
      <div class="flex justify-between"><dt class="text-muted">Household</dt><dd>{profile.household_size}</dd></div>
    {/if}
    {#if profile.lock_in_months_remaining != null}
      <div class="flex justify-between">
        <dt class="text-muted">Lock-in left</dt>
        <dd class={profile.lock_in_months_remaining > 0 ? "text-warn" : "text-muted"}>
          {profile.lock_in_months_remaining} mo
        </dd>
      </div>
    {/if}
  </dl>
</div>
