<script lang="ts">
  import type { EntityProfileStreaming } from "$lib/types";
  import { MonitorPlay } from "lucide-svelte";
  interface Props { profile: EntityProfileStreaming }
  let { profile }: Props = $props();
  const hours = $derived(((profile.monthly_minutes_watched ?? 0) / 60).toFixed(0));
</script>

<div class="card p-4">
  <div class="mb-2 flex items-center gap-2">
    <span class="rounded-md bg-accent2/15 p-1.5 text-accent2"><MonitorPlay size="14" /></span>
    <div class="text-sm font-medium">Acme Streaming</div>
  </div>
  <dl class="space-y-1.5 text-sm">
    <div class="flex justify-between"><dt class="text-muted">Member since</dt><dd>{profile.member_since}</dd></div>
    <div class="flex justify-between"><dt class="text-muted">Watch time</dt><dd>{hours} h / mo</dd></div>
    <div class="flex justify-between"><dt class="text-muted">PPV (30d)</dt><dd>{profile.ppv_count_30d}</dd></div>
  </dl>
  {#if profile.active_apps?.length}
    <div class="mt-2 flex flex-wrap gap-1">
      {#each profile.active_apps as a}
        <span class="pill pill-muted text-[10px]">{a}</span>
      {/each}
    </div>
  {/if}
</div>
