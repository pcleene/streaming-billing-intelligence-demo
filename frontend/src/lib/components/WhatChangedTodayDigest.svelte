<script lang="ts">
  import { onMount } from "svelte";
  import { polishApi } from "$lib/api";
  import type { WhatChangedItem } from "$lib/api";
  import { Newspaper, AlertTriangle, AlertCircle, Sparkles } from "lucide-svelte";
  import { fmtRelative } from "$lib/utils";

  interface Props { hours?: number }
  let { hours = 24 }: Props = $props();

  let items = $state<WhatChangedItem[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function load() {
    loading = true;
    try {
      const r = await polishApi.whatChanged(hours);
      items = r.items ?? [];
      error = null;
    } catch (e: unknown) {
      items = [];
      error = e instanceof Error ? e.message : "what-changed unavailable";
    } finally {
      loading = false;
    }
  }

  function kindMeta(k: string) {
    switch (k) {
      case "drift_alert":
        return { icon: AlertTriangle, tint: "text-warn", label: "drift" };
      case "sla_breach":
        return { icon: AlertCircle, tint: "text-danger", label: "SLA" };
      case "campaign_converted":
        return { icon: Sparkles, tint: "text-ok", label: "conversion" };
      default:
        return { icon: Newspaper, tint: "text-muted", label: k };
    }
  }

  onMount(load);
</script>

<section class="card p-4">
  <header class="mb-3 flex items-center justify-between">
    <div class="flex items-center gap-2">
      <Newspaper size="14" class="text-accent" />
      <h3 class="text-sm font-semibold">What changed today</h3>
    </div>
    <span class="text-[11px] text-muted">last {hours}h</span>
  </header>

  {#if loading}
    <p class="text-xs text-muted">Loading…</p>
  {:else if error}
    <p class="text-xs text-muted">{error}</p>
  {:else if items.length === 0}
    <p class="text-xs text-muted">Nothing notable.</p>
  {:else}
    <ul class="space-y-2">
      {#each items.slice(0, 8) as it}
        {@const m = kindMeta(it.kind)}
        {@const Icon = m.icon}
        <li class="flex items-start gap-2 text-xs">
          <Icon size="12" class={`mt-0.5 shrink-0 ${m.tint}`} />
          <div class="min-w-0 flex-1">
            <p class="truncate">{it.summary}</p>
            <div class="flex items-center gap-2 text-[10px] text-muted">
              <span class="uppercase">{m.label}</span>
              <span>· {fmtRelative(it.ts)}</span>
              {#if it.ref}<a class="font-mono text-accent hover:underline" href={it.ref}>open</a>{/if}
            </div>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>
