<script lang="ts">
  import type { EntityProfilePrepaid } from "$lib/types";
  import { Radio } from "lucide-svelte";
  interface Props { profile: EntityProfilePrepaid }
  let { profile }: Props = $props();
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-warn/15 p-1.5 text-warn"><Radio size="14" /></span>
    <div class="text-sm font-medium">PREPAID</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Activated</dt><dd>{profile.activated_at}</dd></div>
    {#if profile.prepaid_topup_count_30d != null}
      <div class="flex justify-between"><dt class="text-muted">Top-ups (30d)</dt><dd>{profile.prepaid_topup_count_30d}</dd></div>
    {/if}
  </dl>
</div>
