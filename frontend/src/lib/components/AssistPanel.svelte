<script lang="ts">
  import type { AssistResponse } from "$lib/types";
  import ConfidenceBar from "./ConfidenceBar.svelte";
  import { Sparkles } from "lucide-svelte";

  interface Props {
    data: AssistResponse | null;
    loading: boolean;
  }
  let { data, loading }: Props = $props();
</script>

<div class="card-elevated p-5">
  <header class="mb-4 flex items-center gap-2">
    <Sparkles size="16" class="text-accent" />
    <h3 class="text-sm font-semibold uppercase tracking-wide">AI analyst assist</h3>
    {#if data?.degraded}
      <span class="pill pill-warn ml-auto">degraded ({data.reason})</span>
    {/if}
  </header>

  {#if loading}
    <p class="text-sm text-muted">Retrieving similar resolved cases and asking Bedrock…</p>
  {:else if !data}
    <p class="text-sm text-muted">No assist generated yet.</p>
  {:else}
    {@const a = data.assist}
    <p class="text-sm leading-relaxed">{a.summary}</p>

    <div class="mt-4 grid grid-cols-2 gap-4">
      <div>
        <div class="text-xs uppercase text-muted">Likelihood</div>
        <div class="mt-1 font-medium">{a.likelihood}</div>
      </div>
      <div>
        <div class="text-xs uppercase text-muted">Confidence</div>
        <div class="mt-1"><ConfidenceBar value={a.confidence ?? 0} /></div>
      </div>
    </div>

    {#if a.rationale?.length}
      <div class="mt-4">
        <div class="text-xs uppercase text-muted">Rationale</div>
        <ul class="mt-2 list-inside list-disc space-y-1 text-sm">
          {#each a.rationale as r}<li>{r}</li>{/each}
        </ul>
      </div>
    {/if}

    {#if a.recommended_steps?.length}
      <div class="mt-4">
        <div class="text-xs uppercase text-muted">Recommended steps</div>
        <ol class="mt-2 list-inside list-decimal space-y-1 text-sm">
          {#each a.recommended_steps as s}<li>{s}</li>{/each}
        </ol>
      </div>
    {/if}

    {#if a.references?.length}
      <div class="mt-4">
        <div class="text-xs uppercase text-muted">Cited cases</div>
        <ul class="mt-2 space-y-1 text-xs font-mono">
          {#each a.references as r}
            <li class="flex items-center gap-2">
              <span class="text-accent">{r.case_id}</span>
              <span class="pill pill-muted">{r.disposition}</span>
              {#if r.score != null}<span class="text-muted">score {r.score.toFixed(3)}</span>{/if}
              {#if r.why_relevant}<span class="text-muted">— {r.why_relevant}</span>{/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/if}
</div>
