<script lang="ts">
  import JsonTree from "./JsonTree.svelte";
  import AnnotationChip from "./AnnotationChip.svelte";
  import { ChevronRight, ChevronDown } from "lucide-svelte";
  import { annotationFor } from "$lib/inspector-annotations";

  interface Props {
    value: unknown;
    /** Dotted path from the root; used for annotation lookup. */
    path?: string;
    /** Field key in the parent (display only). */
    name?: string;
    /** Render highlighted (used by the page to nudge the eye). */
    highlight?: boolean;
    /** Auto-collapse arrays/objects with more than this many entries. */
    collapseThreshold?: number;
    /** Internal: nesting depth. */
    depth?: number;
  }
  let {
    value,
    path = "",
    name,
    highlight = false,
    collapseThreshold = 5,
    depth = 0
  }: Props = $props();

  function typeOf(v: unknown): string {
    if (v === null) return "null";
    if (Array.isArray(v)) return "array";
    return typeof v;
  }
  const t = $derived(typeOf(value));

  // Start collapsed when nested and large; root + small structures expanded.
  function bigEnoughToCollapse(v: unknown): boolean {
    if (Array.isArray(v)) return v.length > collapseThreshold;
    if (v && typeof v === "object") return Object.keys(v as object).length > collapseThreshold;
    return false;
  }
  let open = $state(depth < 2 && !bigEnoughToCollapse(value));

  const annotation = $derived(path ? annotationFor(path) : undefined);

  function entries(v: unknown): Array<[string, unknown]> {
    if (Array.isArray(v)) return v.map((x, i) => [String(i), x] as [string, unknown]);
    if (v && typeof v === "object") return Object.entries(v as Record<string, unknown>);
    return [];
  }
  const len = $derived(
    Array.isArray(value) ? value.length :
    value && typeof value === "object" ? Object.keys(value as object).length :
    0
  );

  function leafClass(kind: string): string {
    switch (kind) {
      case "string": return "text-emerald-300";
      case "number": return "text-amber-300";
      case "boolean": return "text-cyan-300";
      case "null": return "text-muted italic";
      default: return "text-fg";
    }
  }
  function leafRender(v: unknown, kind: string): string {
    if (kind === "string") return `"${(v as string).length > 240 ? (v as string).slice(0, 240) + "…" : v}"`;
    return String(v);
  }
</script>

<div class={`text-[12px] leading-relaxed font-mono ${highlight ? "ring-1 ring-accent/40 rounded px-1" : ""}`}>
  {#if t === "object" || t === "array"}
    <button
      class="inline-flex items-start gap-1 align-top text-left hover:text-accent"
      onclick={() => (open = !open)}
    >
      {#if open}<ChevronDown size="11" class="mt-1 text-muted" />{:else}<ChevronRight size="11" class="mt-1 text-muted" />{/if}
      {#if name !== undefined}
        <span class="text-fg/90">{name}</span><span class="text-muted">:</span>
      {/if}
      <span class="text-muted">{t === "array" ? `[${len}]` : `{${len}}`}</span>
      {#if annotation}
        <AnnotationChip pattern={annotation.pattern} ref={annotation.ref} blurb={annotation.blurb} />
      {/if}
    </button>

    {#if open}
      <div class="ml-3 border-l border-border/60 pl-3">
        {#each entries(value) as [k, v]}
          <div class="py-0.5">
            <JsonTree
              value={v}
              path={path ? `${path}.${k}` : k}
              name={k}
              depth={depth + 1}
              collapseThreshold={collapseThreshold}
            />
          </div>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="inline-flex items-baseline gap-1">
      {#if name !== undefined}
        <span class="text-fg/90">{name}</span><span class="text-muted">:</span>
      {/if}
      <span class={leafClass(t)}>{leafRender(value, t)}</span>
      {#if annotation}
        <AnnotationChip pattern={annotation.pattern} ref={annotation.ref} blurb={annotation.blurb} />
      {/if}
    </div>
  {/if}
</div>
