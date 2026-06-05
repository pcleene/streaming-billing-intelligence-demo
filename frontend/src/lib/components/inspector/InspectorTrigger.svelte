<script lang="ts">
  import { inspector } from "./stores/inspector.svelte";
  import { Leaf } from "lucide-svelte";

  interface Props {
    /** Best-effort hint shown before any payload is recorded, e.g. "customers_residential.find_one" */
    hint?: string;
  }
  let { hint }: Props = $props();

  const label = $derived(
    inspector.payload
      ? `${inspector.payload.collection}.${inspector.payload.operation}`
      : hint ?? "db.inspect"
  );
</script>

<button
  type="button"
  class={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-mono transition-colors
          ${inspector.pinned ? "border-accent/60 ring-1 ring-accent/40 text-accent" : "border-border bg-elevated/60 text-muted hover:bg-elevated hover:text-fg"}`}
  onclick={() => (inspector.open ? inspector.forceClose() : inspector.openPanel())}
  aria-keyshortcuts="?"
  title="Open MongoDB Inspector (?)"
>
  <Leaf size="11" />
  <span>&lt; {label} &gt;</span>
</button>
