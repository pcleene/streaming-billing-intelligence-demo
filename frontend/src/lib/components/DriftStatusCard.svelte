<script lang="ts">
  import type { DriftStatus, SeverityProgressionStep, DriftSeverity } from "$lib/types";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import { fmtRelative } from "$lib/utils";
  import { Activity, AlertTriangle } from "lucide-svelte";

  interface Props {
    status: DriftStatus | null;
    loading?: boolean;
    error?: string | null;
  }
  let { status, loading = false, error = null }: Props = $props();

  /** KS statistic clamped to [0,1] for the gauge fill. */
  const ksPct = $derived(
    status ? Math.max(0, Math.min(1, status.ks_statistic)) * 100 : 0
  );

  const gaugeColor = $derived(
    status?.severity === "alert"
      ? "bg-danger"
      : status?.severity === "warn"
        ? "bg-warn"
        : status?.severity === "watch"
          ? "bg-accent"
          : "bg-ok"
  );

  /** Severity-band thresholds — match `feature_drift_detector.SEVERITY_BANDS`. */
  const BAND_MARKERS: { label: string; pct: number; tone: string }[] = [
    { label: "watch", pct: 10, tone: "border-accent/60" },
    { label: "warn", pct: 20, tone: "border-warn/60" },
    { label: "alert", pct: 40, tone: "border-danger/60" }
  ];

  /** Map a severity to the same chip class used elsewhere in the app. */
  function severityChip(sev: DriftSeverity | string | undefined): string {
    return sev === "alert"
      ? "pill pill-danger"
      : sev === "warn"
        ? "pill pill-warn"
        : sev === "watch"
          ? "pill pill-accent"
          : sev === "none"
            ? "pill pill-ok"
            : "pill pill-muted";
  }

  function actionChip(action: string | undefined): string {
    if (!action) return "pill pill-muted";
    if (action === "alert_oncall" || action === "retrain") return "pill pill-danger";
    if (action === "investigate") return "pill pill-warn";
    return "pill pill-muted";
  }

  /** Normalise both runtime (`{at, from, to}`) and legacy (`{at, severity}`)
   *  progression shapes into a uniform `{severity, at}[]` for rendering. */
  function normalizeProgression(
    steps: SeverityProgressionStep[] | undefined | null
  ): { severity: string; at: string }[] {
    if (!steps || steps.length === 0) return [];
    const first = steps[0];
    const isFromTo = first.from !== undefined && first.to !== undefined;
    if (isFromTo) {
      return [
        { severity: String(first.from), at: first.at },
        ...steps.map((s) => ({ severity: String(s.to), at: s.at }))
      ];
    }
    return steps.map((s) => ({ severity: String(s.severity ?? "none"), at: s.at }));
  }

  const timeline = $derived(normalizeProgression(status?.severity_progression));

  /** "Drifted Nh ago" — relative to the first non-`none` transition. */
  const driftStartedAt = $derived(
    timeline.find((s) => s.severity !== "none")?.at ?? null
  );
</script>

<div class="card p-5">
  <header class="mb-4 flex items-start justify-between gap-3">
    <div class="flex items-center gap-2">
      <Activity class="h-4 w-4 text-accent" />
      <h2 class="text-lg font-semibold">Drift status</h2>
    </div>
    {#if status}
      <div class="flex flex-wrap items-center justify-end gap-1.5">
        {#if status.recommended_action}
          <span
            class={actionChip(status.recommended_action)}
            title="Recommended action — derived from severity + downstream consumers"
          >
            {status.recommended_action}
          </span>
        {/if}
        <SeverityBadge severity={status.severity} />
      </div>
    {/if}
  </header>

  {#if loading}
    <div class="space-y-3">
      <div class="h-4 w-1/3 animate-pulse rounded bg-bg/60"></div>
      <div class="h-3 w-full animate-pulse rounded bg-bg/60"></div>
      <div class="h-3 w-2/3 animate-pulse rounded bg-bg/60"></div>
    </div>
  {:else if error}
    <div class="flex items-center gap-2 text-sm text-danger">
      <AlertTriangle class="h-4 w-4" />
      <span>{error}</span>
    </div>
  {:else if status}
    <div class="space-y-5">
      <!-- KS gauge with band markers -->
      <div>
        <div class="flex items-baseline justify-between text-xs text-muted">
          <span>KS statistic</span>
          <span class="tabular-nums text-fg">{status.ks_statistic.toFixed(3)}</span>
        </div>
        <div class="relative mt-1 h-2 w-full overflow-hidden rounded-full bg-bg/60">
          <div
            class="h-full {gaugeColor} transition-all"
            style="width: {ksPct}%"
          ></div>
          {#each BAND_MARKERS as m}
            <div
              class="absolute top-0 h-full border-r {m.tone}"
              style="left: {m.pct}%"
              title="{m.label} threshold ≥ {(m.pct / 100).toFixed(2)}"
            ></div>
          {/each}
        </div>
        <div class="mt-1 flex justify-between text-[10px] text-muted">
          <span>0.0</span>
          <span title="watch threshold">0.10</span>
          <span title="warn threshold">0.20</span>
          <span title="alert threshold">0.40</span>
          <span>1.0</span>
        </div>
        {#if typeof status.p_value === "number"}
          <div class="mt-1 text-[11px] text-muted">
            p ≈ {status.p_value < 0.001 ? "<0.001" : status.p_value.toFixed(3)} —
            {status.p_value < 0.001
              ? "very unlikely to be noise"
              : status.p_value < 0.05
                ? "likely a real shift"
                : "could be noise"}
          </div>
        {/if}
      </div>

      <!-- Status grid -->
      <div class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <div class="text-xs uppercase tracking-wide text-muted">Drift detected</div>
          <div class="mt-1 font-semibold {status.drift_detected ? 'text-danger' : 'text-ok'}">
            {status.drift_detected ? "Yes" : "No"}
          </div>
        </div>
        <div>
          <div class="text-xs uppercase tracking-wide text-muted">Last observed</div>
          <div class="mt-1 text-fg">{fmtRelative(status.last_observed_at)}</div>
        </div>
        {#if driftStartedAt}
          <div>
            <div class="text-xs uppercase tracking-wide text-muted">Drift started</div>
            <div class="mt-1 text-fg">{fmtRelative(driftStartedAt)}</div>
          </div>
        {/if}
        {#if status.sample_size_current}
          <div>
            <div class="text-xs uppercase tracking-wide text-muted">Sample sizes</div>
            <div class="mt-1 text-xs tabular-nums text-fg">
              current {status.sample_size_current.toLocaleString()} ·
              baseline {(status.sample_size_baseline ?? 0).toLocaleString()}
            </div>
          </div>
        {/if}
      </div>

      <!-- Severity timeline (horizontal, with relative timestamps) -->
      {#if timeline.length > 0}
        <div>
          <div class="mb-2 text-xs uppercase tracking-wide text-muted">
            Severity progression
          </div>
          <ol class="flex flex-wrap items-center gap-2 text-xs">
            {#each timeline as step, i}
              <li class="flex items-center gap-2">
                <span class={severityChip(step.severity)} title={step.at}>
                  <span class="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
                  {step.severity}
                </span>
                {#if i < timeline.length - 1}
                  <span class="flex flex-col items-center text-muted">
                    <span class="text-[10px]">{fmtRelative(timeline[i + 1].at)}</span>
                    <span class="font-mono text-xs leading-none">→</span>
                  </span>
                {/if}
              </li>
            {/each}
          </ol>
        </div>
      {/if}
    </div>
  {:else}
    <div class="text-sm text-muted">No drift status available.</div>
  {/if}
</div>
