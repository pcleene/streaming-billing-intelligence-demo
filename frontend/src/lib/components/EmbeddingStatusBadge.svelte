<script lang="ts">
  import { Database } from "lucide-svelte";
  import { customersRefreshApi } from "$lib/api";
  import type { EmbeddingStatus } from "$lib/types";
  import { fmtDate } from "$lib/utils";

  interface Props {
    customerId: string;
  }
  let { customerId }: Props = $props();

  let status = $state<EmbeddingStatus | null>(null);
  let err = $state<string | null>(null);
  let loading = $state(false);

  async function load() {
    if (!customerId) return;
    loading = true;
    err = null;
    try {
      status = await customersRefreshApi.embeddingStatus(customerId);
    } catch (e) {
      err = e instanceof Error ? e.message : String(e);
      status = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    void customerId;
    load();
  });

  const label = $derived.by(() => {
    if (!status) return loading ? "embedding…" : "embedding ?";
    if (!status.has_embedding) return "missing";
    return status.is_stale ? "stale" : "fresh";
  });

  const tone = $derived.by(() => {
    if (!status) return "border-border text-muted bg-elevated";
    if (!status.has_embedding) return "border-danger/40 text-danger bg-danger/10";
    if (status.is_stale) return "border-warn/40 text-warn bg-warn/10";
    return "border-ok/40 text-ok bg-ok/10";
  });

  const tooltip = $derived.by(() => {
    if (err) return `Error: ${err}`;
    if (!status) return "Loading embedding status…";
    const parts: string[] = [];
    parts.push(`has_embedding: ${status.has_embedding}`);
    if (status.dim != null) parts.push(`dim: ${status.dim}`);
    if (status.generated_at) parts.push(`generated_at: ${fmtDate(status.generated_at)}`);
    if (status.age_seconds != null) parts.push(`age: ${Math.round(status.age_seconds)}s`);
    parts.push(`is_stale: ${status.is_stale}`);
    return parts.join("\n");
  });
</script>

<span
  data-testid="embedding-status-badge"
  title={tooltip}
  class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium {tone}"
>
  <Database class="h-3 w-3" aria-hidden="true" />
  <span>embedding: {label}</span>
  {#if status?.dim}<span class="font-mono opacity-70">·{status.dim}d</span>{/if}
</span>
