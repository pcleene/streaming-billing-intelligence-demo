<script lang="ts">
  import { atlasIndexHealthApi } from "$lib/api";
  import type { AtlasIndexHealth } from "$lib/types";
  import { Database, CheckCircle2, RefreshCw, AlertTriangle } from "lucide-svelte";
  import { onDestroy } from "svelte";

  let health = $state<AtlasIndexHealth | null>(null);
  let error = $state<string | null>(null);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function tick() {
    try {
      health = await atlasIndexHealthApi.get();
      error = null;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "unavailable";
      // Keep last-good payload if any; mark explicit unknown otherwise.
      if (!health) {
        health = { indexes: [], overall: "unknown", checked_at: new Date().toISOString() };
      }
    }
  }

  $effect(() => {
    tick();
    timer = setInterval(tick, 60_000);
    return () => { if (timer) clearInterval(timer); timer = null; };
  });

  onDestroy(() => { if (timer) clearInterval(timer); });

  const overall = $derived(health?.overall ?? "unknown");
  const dotClass = $derived(
    overall === "ready" ? "bg-ok" :
    overall === "syncing" ? "bg-warn" :
    overall === "failed" ? "bg-danger" :
    "bg-muted/60"
  );
  const Icon = $derived(
    overall === "ready" ? CheckCircle2 :
    overall === "syncing" ? RefreshCw :
    overall === "failed" ? AlertTriangle :
    Database
  );
  const label = $derived(
    overall === "ready" ? "Indexes READY" :
    overall === "syncing" ? "Syncing" :
    overall === "failed" ? "Index failed" :
    error ? "Health unknown" : "Checking…"
  );

  const failedNames = $derived(
    (health?.indexes ?? [])
      .filter((i) => i.state === "FAILED")
      .map((i) => `${i.collection}.${i.index_name}`)
      .join(", ")
  );
</script>

<div
  class="inline-flex items-center gap-2 rounded-full border border-border bg-elevated/70 px-2.5 py-1 text-[11px] text-muted hover:text-fg transition-colors"
  title={failedNames ? `Failed: ${failedNames}` : (error ?? `${health?.indexes?.length ?? 0} indexes`)}
>
  <span class="relative inline-flex h-2 w-2">
    <span class="absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping {dotClass}"></span>
    <span class="relative inline-flex h-2 w-2 rounded-full {dotClass}"></span>
  </span>
  <Icon size="12" class={overall === "syncing" ? "animate-spin" : ""} />
  <span>{label}</span>
</div>
