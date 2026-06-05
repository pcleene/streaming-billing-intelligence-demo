<script lang="ts">
  import { quarantineAssistApi } from "$lib/api";
  import type { BatchAssistResult } from "$lib/types";
  import Section from "$lib/components/Section.svelte";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import { Send, AlertCircle } from "lucide-svelte";

  const MAX_IDS = 25;

  let raw = $state("");
  let force = $state(false);
  let submitting = $state(false);
  let error = $state<string | null>(null);
  let result = $state<BatchAssistResult | null>(null);

  const ids = $derived.by(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const line of raw.split(/\r?\n|,/)) {
      const t = line.trim();
      if (!t) continue;
      if (seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  });

  const count = $derived(ids.length);
  const overLimit = $derived(count > MAX_IDS);
  const canSubmit = $derived(count > 0 && !overLimit && !submitting);

  async function submit() {
    if (!canSubmit) return;
    submitting = true;
    error = null;
    result = null;
    try {
      result = await quarantineAssistApi.batchAiAssist(ids, force);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "batch ai-assist failed";
    } finally {
      submitting = false;
    }
  }
</script>

<div class="space-y-6">
  <header class="flex items-start justify-between gap-3">
    <div>
      <h1 class="text-2xl font-semibold">Batch AI assist</h1>
      <p class="text-sm text-muted">
        Run AI analyst assist over up to {MAX_IDS} quarantine cases at once.
      </p>
    </div>
    <a class="btn" href="/quarantine">Back to queue</a>
  </header>

  <Section title="Case IDs" subtitle="One per line (commas also accepted). Duplicates removed.">
    <div class="space-y-3">
      <textarea
        class="input min-h-40 font-mono text-sm"
        bind:value={raw}
        placeholder={"case_abc123\ncase_def456\n…"}
      ></textarea>

      <div class="flex flex-wrap items-center gap-3">
        <span
          class="pill {overLimit ? 'pill-danger' : count === 0 ? 'pill-muted' : 'pill-accent'}"
          data-testid="id-count-pill"
        >
          {count} {count === 1 ? "id" : "ids"}{overLimit ? ` — over ${MAX_IDS}` : ""}
        </span>

        <label class="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" bind:checked={force} />
          <span>Force regenerate (override existing assist)</span>
        </label>

        <button
          type="button"
          class="btn btn-primary ml-auto inline-flex items-center gap-2"
          onclick={submit}
          disabled={!canSubmit}
          data-testid="batch-submit"
        >
          <Send size="14" />
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </div>

      {#if overLimit}
        <p class="inline-flex items-center gap-1 text-xs text-danger">
          <AlertCircle size="12" />
          Cap is {MAX_IDS} ids per batch — remove {count - MAX_IDS} to proceed.
        </p>
      {/if}
    </div>
  </Section>

  {#if error}
    <div class="card p-3 text-sm text-danger">{error}</div>
  {/if}

  {#if result}
    <Section title="Result">
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <KpiTile label="Requested" value={result.requested} accent="accent" />
        <KpiTile label="Generated" value={result.generated} accent="ok" />
        <KpiTile
          label="Skipped"
          value={result.skipped}
          accent={result.skipped > 0 ? "warn" : "default"}
        />
      </div>

      {#if result.errors?.length}
        <div class="mt-4">
          <div class="text-xs uppercase text-muted">Errors</div>
          <div class="mt-2 overflow-hidden rounded-lg border border-border">
            <table class="w-full text-left text-sm">
              <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th class="px-3 py-2">Case</th>
                  <th class="px-3 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {#each result.errors as e}
                  <tr class="border-t border-border">
                    <td class="px-3 py-2">
                      <a class="font-mono text-accent hover:underline" href={`/quarantine/${e.case_id}`}>
                        {e.case_id}
                      </a>
                    </td>
                    <td class="px-3 py-2 text-muted">{e.reason}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </div>
      {:else}
        <p class="mt-4 text-sm text-muted">No errors.</p>
      {/if}
    </Section>
  {/if}
</div>
