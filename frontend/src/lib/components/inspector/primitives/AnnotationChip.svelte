<script lang="ts">
  import type { AnnotationPattern } from "$lib/inspector-annotations";
  import { PATTERN_LABEL, PATTERN_TINT } from "$lib/inspector-annotations";

  interface Props {
    pattern: AnnotationPattern;
    ref?: string;
    blurb?: string;
  }
  let { pattern, ref, blurb }: Props = $props();

  let hover = $state(false);
</script>

<span
  class={`pill text-[10px] border ${PATTERN_TINT[pattern]} relative cursor-help`}
  onmouseenter={() => (hover = true)}
  onmouseleave={() => (hover = false)}
  role="note"
>
  {PATTERN_LABEL[pattern]}{#if ref}<span class="ml-1 opacity-70">{ref}</span>{/if}
  {#if hover && blurb}
    <span class="absolute left-0 top-full z-50 mt-1 w-56 rounded-md border border-border bg-elevated p-2 text-[11px] font-normal text-fg shadow-lg">
      {blurb}
    </span>
  {/if}
</span>
