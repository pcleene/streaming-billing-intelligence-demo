<script lang="ts">
  interface Props {
    stage: string; // e.g. "$match", "$vectorSearch"
    forbidden?: boolean; // red dot when we catch a $lookup (ADR-011)
  }
  let { stage, forbidden = false }: Props = $props();
</script>

<span class="pill bg-elevated text-fg border border-border font-mono text-[11px] inline-flex items-center gap-1">
  {#if forbidden}
    <span class="inline-block h-1.5 w-1.5 rounded-full bg-danger" title="$lookup is forbidden by ADR-011"></span>
  {/if}
  {stage}
</span>
