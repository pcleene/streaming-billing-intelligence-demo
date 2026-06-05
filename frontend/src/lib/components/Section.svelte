<script lang="ts">
  import type { Snippet } from "svelte";
  interface Props {
    title: string;
    subtitle?: string;
    actions?: Snippet;
    children: Snippet;
  }
  let { title, subtitle, actions, children }: Props = $props();
</script>

<section class="card p-5">
  <header class="mb-4 flex items-start justify-between gap-3">
    <div>
      <h2 class="text-lg font-semibold">{title}</h2>
      {#if subtitle}<p class="text-xs text-muted">{subtitle}</p>{/if}
    </div>
    {#if actions}
      <div class="shrink-0">
        {@render actions()}
      </div>
    {/if}
  </header>
  {@render children()}
</section>
