<script lang="ts">
  import type { EntityProfileBroadband } from "$lib/types";
  import { Wifi } from "lucide-svelte";
  interface Props { profile: EntityProfileBroadband }
  let { profile }: Props = $props();
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-ok/15 p-1.5 text-ok"><Wifi size="14" /></span>
    <div class="text-sm font-medium">Acme Broadband</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Member since</dt><dd>{profile.member_since}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Downstream</dt><dd>{profile.downstream_mbps} Mbps</dd></div>
    {#if profile.data_cap_gb != null}
      <div class="flex justify-between"><dt class="text-muted">Data cap</dt><dd>{profile.data_cap_gb} GB</dd></div>
    {/if}
  </dl>
</div>
