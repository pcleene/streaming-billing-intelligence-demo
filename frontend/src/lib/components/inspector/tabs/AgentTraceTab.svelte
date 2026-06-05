<script lang="ts">
  import type { InspectorPayload } from "$lib/components/inspector/stores/inspector.svelte";
  import NodeFlowDiagram from "$lib/components/inspector/primitives/NodeFlowDiagram.svelte";
  import NodeAccordion from "$lib/components/inspector/primitives/NodeAccordion.svelte";

  interface Props { payload: InspectorPayload | null }
  let { payload }: Props = $props();

  const trace = $derived(payload?.agent_trace ?? []);

  // Best-effort: extract `mode` and `classify` reason from the classify node's metadata.
  const classify = $derived(trace.find((n) => n.node === "classify"));
  const mode = $derived<string | undefined>(
    (classify?.metadata?.mode as string | undefined) ??
    (classify?.metadata?.result as string | undefined)
  );
  const reason = $derived<string | undefined>(classify?.metadata?.reason as string | undefined);
</script>

{#if !payload || trace.length === 0}
  <p class="text-sm text-muted">No agent trace on this request.</p>
{:else}
  <div class="space-y-3">
    <NodeFlowDiagram nodes={trace} mode={mode} classifyReason={reason} />
    <NodeAccordion nodes={trace} />
  </div>
{/if}
