<script lang="ts">
  /**
   * Tiny pure-SVG sparkline. Stateless. No deps, no axes — just a thin
   * polyline plus min/max annotations on the right. Used by the burst
   * metrics detail page (`/metrics`) to show TPS and p99 over time.
   */
  interface Props {
    points: number[];
    width?: number;
    height?: number;
    label?: string;
    unit?: string;
    /** Tailwind text-color class for the line (uses currentColor). */
    accent?: string;
    /** Optional fixed minimum (otherwise derived from data). */
    minOverride?: number | null;
  }
  let {
    points,
    width = 600,
    height = 100,
    label = "",
    unit = "",
    accent = "text-accent",
    minOverride = null
  }: Props = $props();

  const PAD = 6;

  const stats = $derived.by(() => {
    if (!points || points.length === 0) {
      return { min: 0, max: 0, last: 0 };
    }
    const min = minOverride ?? Math.min(...points);
    const max = Math.max(...points);
    return { min, max, last: points[points.length - 1] };
  });

  const path = $derived.by(() => {
    if (!points || points.length === 0) return "";
    const min = stats.min;
    const max = stats.max;
    const range = Math.max(1e-9, max - min);
    const innerW = width - 2 * PAD;
    const innerH = height - 2 * PAD;
    const stepX = innerW / Math.max(1, points.length - 1);
    return points
      .map((v, i) => {
        const x = PAD + i * stepX;
        const y = PAD + innerH - ((v - min) / range) * innerH;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  });

  function fmt(v: number): string {
    if (!Number.isFinite(v)) return "—";
    if (Math.abs(v) >= 100) return v.toFixed(0);
    if (Math.abs(v) >= 10) return v.toFixed(1);
    return v.toFixed(2);
  }
</script>

<div class="card p-3">
  {#if label}
    <div class="mb-1 flex items-center justify-between text-xs">
      <span class="uppercase tracking-wide text-muted">{label}</span>
      <span class="text-muted">
        last <span class="text-fg">{fmt(stats.last)}</span>
        {unit ? ` ${unit}` : ""}
      </span>
    </div>
  {/if}

  <div class="flex items-stretch gap-3">
    <svg
      viewBox="0 0 {width} {height}"
      class="w-full {accent}"
      preserveAspectRatio="none"
      role="img"
      aria-label={label ? `${label} sparkline` : "sparkline"}
    >
      {#if path}
        <path
          d={path}
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linejoin="round"
          stroke-linecap="round"
        />
      {:else}
        <line
          x1={PAD}
          y1={height / 2}
          x2={width - PAD}
          y2={height / 2}
          stroke="currentColor"
          stroke-dasharray="2 4"
          stroke-width="1"
          opacity="0.4"
        />
      {/if}
    </svg>

    <div class="flex w-14 shrink-0 flex-col justify-between text-[10px] leading-tight text-muted">
      <div class="text-right">
        <div class="text-fg">{fmt(stats.max)}</div>
        <div>max</div>
      </div>
      <div class="text-right">
        <div class="text-fg">{fmt(stats.min)}</div>
        <div>min</div>
      </div>
    </div>
  </div>
</div>
