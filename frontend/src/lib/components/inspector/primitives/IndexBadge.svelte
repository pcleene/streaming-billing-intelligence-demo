<script lang="ts">
  interface Props {
    stage: string | null | undefined;
    indexName?: string | null;
  }
  let { stage, indexName }: Props = $props();
  const isVector = $derived((stage ?? "").toUpperCase() === "VECTOR_SEARCH");
  const isCollscan = $derived((stage ?? "").toUpperCase() === "COLLSCAN");
  const tone = $derived(
    isCollscan ? "danger" :
    (stage ?? "").toUpperCase().includes("FETCH") ? "warn" : "ok"
  );
  const tintClass = $derived(
    tone === "ok" ? "bg-ok/15 text-ok border-ok/30" :
    tone === "warn" ? "bg-warn/15 text-warn border-warn/30" :
    "bg-danger/15 text-danger border-danger/30"
  );
</script>

<span
  class={`pill text-[11px] font-mono border inline-flex items-center gap-1 ${tintClass}`}
  title={indexName ?? (isVector ? "Atlas vector index" : "Mongo plan stage")}
>
  {stage ?? "—"}
  {#if indexName}
    <span class="opacity-70">· {indexName}</span>
  {/if}
</span>
