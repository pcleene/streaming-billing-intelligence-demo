<script lang="ts">
  import type { DriftHistoryPoint } from "$lib/api";

  interface Props {
    points: DriftHistoryPoint[];
    height?: number;
  }
  let { points, height = 160 }: Props = $props();

  // Vanilla SVG line chart of ks_statistic over time. No deps; matches the
  // house "no chart library" convention in the rest of the dashboard.
  const w = 720;
  const padX = 32;
  const padY = 22;

  // Severity-band thresholds — match `feature_drift_detector.SEVERITY_BANDS`.
  const BANDS = [
    { label: "watch", value: 0.1, color: "var(--accent)" },
    { label: "warn", value: 0.2, color: "var(--warn)" },
    { label: "alert", value: 0.4, color: "var(--danger)" }
  ];

  const xs = $derived(points.map((_, i) => i));
  const ys = $derived(points.map((p) => p.ks_statistic));
  // Always include the alert threshold in the y-axis range so the bands
  // are visible even when the actual KS values are tiny.
  const maxY = $derived(Math.max(0.45, ...ys) * 1.05);
  const minY = 0;
  const maxX = $derived(Math.max(1, xs.length - 1));

  function xOf(i: number) {
    return padX + (i / maxX) * (w - padX * 2);
  }
  function yOf(v: number) {
    const span = maxY - minY;
    return height - padY - ((v - minY) / span) * (height - padY * 2);
  }

  const linePath = $derived.by(() => {
    if (points.length === 0) return "";
    return points
      .map(
        (p, i) =>
          `${i === 0 ? "M" : "L"} ${xOf(i).toFixed(2)} ${yOf(p.ks_statistic).toFixed(2)}`
      )
      .join(" ");
  });

  // Area fill underneath the line — improves readability for dense series.
  const areaPath = $derived.by(() => {
    if (points.length === 0) return "";
    const top = points
      .map((p, i) => `${i === 0 ? "M" : "L"} ${xOf(i).toFixed(2)} ${yOf(p.ks_statistic).toFixed(2)}`)
      .join(" ");
    const baseY = (height - padY).toFixed(2);
    return `${top} L ${xOf(points.length - 1).toFixed(2)} ${baseY} L ${xOf(0).toFixed(2)} ${baseY} Z`;
  });

  const sevColor: Record<string, string> = {
    none: "var(--ok)",
    watch: "var(--accent)",
    warn: "var(--warn)",
    alert: "var(--danger)"
  };

  /** Sample down dot rendering when there are many points; otherwise the
   *  chart becomes a wall of circles. We always keep the latest point. */
  const dotIndices = $derived.by(() => {
    if (points.length <= 50) return points.map((_, i) => i);
    const step = Math.ceil(points.length / 50);
    const idxs = [];
    for (let i = 0; i < points.length; i += step) idxs.push(i);
    if (idxs[idxs.length - 1] !== points.length - 1) idxs.push(points.length - 1);
    return idxs;
  });

  /** First / last timestamp labels for the x-axis. */
  const firstLabel = $derived(points[0]?.measured_at ?? "");
  const lastLabel = $derived(points[points.length - 1]?.measured_at ?? "");

  function shortDate(iso: string): string {
    if (!iso) return "";
    const ts = Date.parse(iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z");
    if (Number.isNaN(ts)) return iso;
    return new Date(ts).toLocaleDateString("en-MY", {
      month: "short",
      day: "numeric"
    });
  }
</script>

<div class="rounded-md border border-border bg-elevated/40 p-3">
  <div class="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-muted">
    <span>KS statistic — last {points.length} measurements</span>
    <span class="flex items-center gap-3 normal-case">
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full" style="background: var(--accent)"></span>
        watch
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full" style="background: var(--warn)"></span>
        warn
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full" style="background: var(--danger)"></span>
        alert
      </span>
    </span>
  </div>

  {#if points.length === 0}
    <p class="text-xs text-muted">No history.</p>
  {:else}
    <svg viewBox={`0 0 ${w} ${height}`} class="w-full" preserveAspectRatio="none">
      <!-- Severity-band reference lines + labels -->
      {#each BANDS as b}
        <line
          x1={padX}
          y1={yOf(b.value)}
          x2={w - padX}
          y2={yOf(b.value)}
          stroke={b.color}
          stroke-width="1"
          stroke-dasharray="4 4"
          opacity="0.4"
        />
        <text
          x={padX - 4}
          y={yOf(b.value) + 3}
          text-anchor="end"
          font-size="9"
          fill="currentColor"
          class="text-muted"
        >
          {b.value.toFixed(2)}
        </text>
      {/each}

      <!-- Area + line -->
      <path d={areaPath} fill="currentColor" class="text-accent" opacity="0.08" />
      <path d={linePath} fill="none" stroke="currentColor" class="text-accent" stroke-width="1.5" />

      <!-- X-axis baseline -->
      <line
        x1={padX}
        y1={height - padY}
        x2={w - padX}
        y2={height - padY}
        stroke="currentColor"
        class="text-border"
        stroke-width="1"
      />

      <!-- Sampled severity-coloured dots -->
      {#each dotIndices as i}
        {@const p = points[i]}
        <circle
          cx={xOf(i)}
          cy={yOf(p.ks_statistic)}
          r={i === points.length - 1 ? 3 : 2}
          style={`fill: ${sevColor[p.severity] ?? "var(--fg)"}`}
        >
          <title>{p.measured_at}: KS={p.ks_statistic.toFixed(3)} ({p.severity})</title>
        </circle>
      {/each}

      <!-- X-axis labels -->
      <text x={padX} y={height - 4} font-size="9" fill="currentColor" class="text-muted">
        {shortDate(firstLabel)}
      </text>
      <text
        x={w - padX}
        y={height - 4}
        text-anchor="end"
        font-size="9"
        fill="currentColor"
        class="text-muted"
      >
        {shortDate(lastLabel)}
      </text>
    </svg>
  {/if}
</div>
