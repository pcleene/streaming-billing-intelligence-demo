<script lang="ts">
  import { inspector } from "./stores/inspector.svelte";
  import LatencyBadge from "./primitives/LatencyBadge.svelte";

  function copyMongosh() {
    const p = inspector.payload;
    if (!p) return;
    const dbColl = `db.${p.collection}`;
    let script: string;
    if (Array.isArray(p.query)) {
      script = `${dbColl}.aggregate(${JSON.stringify(p.query, null, 2)})`;
    } else if (p.operation === "find_one") {
      script = `${dbColl}.findOne(${JSON.stringify(p.query, null, 2)})`;
    } else {
      script = `${dbColl}.${p.operation}(${JSON.stringify(p.query, null, 2)})`;
    }
    try { navigator.clipboard.writeText(script); } catch { /* ignore */ }
  }
</script>

<footer class="flex items-center justify-between border-t border-border bg-surface px-3 py-2 text-[11px]">
  {#if inspector.payload}
    <div class="inline-flex items-center gap-2">
      <LatencyBadge ms={inspector.payload.latency_ms} />
      <span class="text-muted">{inspector.payload.result_count} docs · {(inspector.payload.result_bytes / 1024).toFixed(1)} KB</span>
    </div>
    <button class="btn btn-primary text-[11px]" onclick={copyMongosh}>Copy as mongosh</button>
  {:else}
    <span class="text-muted">Waiting for a fetch…</span>
  {/if}
</footer>
