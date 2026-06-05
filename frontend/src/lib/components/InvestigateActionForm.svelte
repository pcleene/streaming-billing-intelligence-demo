<script lang="ts">
  import { driftApi } from "$lib/api";
  import type {
    InvestigateAction,
    InvestigateActionPayload,
    InvestigateActionResult
  } from "$lib/types";
  import { fmtDate } from "$lib/utils";
  import { AlertTriangle } from "lucide-svelte";

  interface Props {
    featureName: string;
  }
  let { featureName }: Props = $props();

  let action = $state<InvestigateAction>("acknowledge");
  let note = $state("");
  let snoozeUntil = $state("");
  let submitting = $state(false);
  let result = $state<InvestigateActionResult | null>(null);
  let err = $state<string | null>(null);

  const isSnooze = $derived(action === "snooze");

  async function submit(e: Event) {
    e.preventDefault();
    submitting = true;
    err = null;
    result = null;
    const payload: InvestigateActionPayload = { action };
    if (note.trim()) payload.note = note.trim();
    if (isSnooze && snoozeUntil) payload.snooze_until = snoozeUntil;
    try {
      result = await driftApi.investigateAction(featureName, payload);
    } catch (ex) {
      err = ex instanceof Error ? ex.message : String(ex);
    } finally {
      submitting = false;
    }
  }
</script>

<div class="card p-5">
  <header class="mb-4">
    <h2 class="text-lg font-semibold">Investigate action</h2>
    <p class="text-xs text-muted">Record analyst response on this drift signal.</p>
  </header>

  <form class="space-y-4" onsubmit={submit}>
    <div>
      <label class="mb-1 block text-xs uppercase tracking-wide text-muted" for="ia-action">
        Action
      </label>
      <select
        id="ia-action"
        class="input"
        bind:value={action}
        disabled={submitting}
      >
        <option value="acknowledge">Acknowledge</option>
        <option value="snooze">Snooze</option>
        <option value="escalate">Escalate</option>
      </select>
    </div>

    <div>
      <label class="mb-1 block text-xs uppercase tracking-wide text-muted" for="ia-note">
        Note (optional)
      </label>
      <textarea
        id="ia-note"
        class="input min-h-[80px]"
        placeholder="Context for the audit log…"
        bind:value={note}
        disabled={submitting}
      ></textarea>
    </div>

    <div>
      <label class="mb-1 block text-xs uppercase tracking-wide text-muted" for="ia-snooze">
        Snooze until {isSnooze ? "(required)" : "(only when action = snooze)"}
      </label>
      <input
        id="ia-snooze"
        type="datetime-local"
        class="input"
        bind:value={snoozeUntil}
        disabled={!isSnooze || submitting}
      />
    </div>

    <div class="flex items-center gap-3">
      <button class="btn btn-primary" type="submit" disabled={submitting}>
        {submitting ? "Submitting…" : "Submit action"}
      </button>
      {#if err}
        <span class="flex items-center gap-1 text-sm text-danger">
          <AlertTriangle class="h-4 w-4" />
          {err}
        </span>
      {/if}
    </div>

    {#if result}
      <div
        class="rounded-md border border-ok/40 bg-ok/10 px-3 py-2 text-sm"
        data-testid="ia-success"
      >
        <span class="font-medium text-ok">Recorded</span>
        <span class="text-muted"> · {result.action}</span>
        <span class="text-muted"> · {fmtDate(result.recorded_at)}</span>
      </div>
    {/if}
  </form>
</div>
