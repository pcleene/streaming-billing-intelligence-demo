<script lang="ts">
  interface Props { value: number; }   // 0..1
  let { value }: Props = $props();
  const pct = $derived(Math.max(0, Math.min(1, value)) * 100);
  const tone = $derived(value < 0.4 ? "warn" : value < 0.7 ? "accent" : "ok");
</script>

<div class="flex items-center gap-2">
  <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-elevated">
    <div
      class="h-full"
      style:width={`${pct}%`}
      class:bg-warn={tone === "warn"}
      class:bg-accent={tone === "accent"}
      class:bg-ok={tone === "ok"}
    ></div>
  </div>
  <span class="text-xs text-muted tabular-nums">{(value * 100).toFixed(0)}%</span>
</div>
