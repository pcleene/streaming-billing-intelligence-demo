<script lang="ts">
  import { Zap } from "lucide-svelte";
  interface Props { ms: number }
  let { ms }: Props = $props();
  const tone = $derived(ms < 50 ? "ok" : ms < 200 ? "warn" : "danger");
  const tintClass = $derived(
    tone === "ok" ? "bg-ok/15 text-ok border-ok/30" :
    tone === "warn" ? "bg-warn/15 text-warn border-warn/30" :
    "bg-danger/15 text-danger border-danger/30"
  );
</script>

<span class={`pill text-[11px] border inline-flex items-center gap-1 ${tintClass}`} title="Server-side latency">
  <Zap size="11" />
  {ms.toFixed(1)} ms
</span>
