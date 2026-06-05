<script lang="ts">
  // Vanilla SVG sparkline. Color follows the trend direction:
  // last - first > 0 -> ok (green), < 0 -> danger (red), 0 -> muted.
  interface Props {
    points: number[];
    width?: number;
    height?: number;
    strokeWidth?: number;
    ariaLabel?: string;
  }
  let { points, width = 240, height = 32, strokeWidth = 1.5, ariaLabel = "" }: Props = $props();

  const stats = $derived.by(() => {
    if (!points || points.length === 0) return { min: 0, max: 1, trend: 0 };
    const min = Math.min(...points);
    const max = Math.max(...points);
    return { min, max, trend: points[points.length - 1] - points[0] };
  });

  const stroke = $derived(
    stats.trend > 0 ? "rgb(46 199 144)" : stats.trend < 0 ? "rgb(240 90 90)" : "rgb(133 137 168)"
  );

  const path = $derived.by(() => {
    if (!points || points.length === 0) return "";
    const range = Math.max(stats.max - stats.min, 1e-9);
    const stepX = points.length > 1 ? width / (points.length - 1) : width;
    return points
      .map((p, i) => {
        const x = i * stepX;
        const y = height - ((p - stats.min) / range) * height;
        return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  });

  const areaPath = $derived.by(() => {
    if (!points || points.length === 0 || !path) return "";
    return `${path} L${width},${height} L0,${height} Z`;
  });
</script>

{#if points && points.length > 0}
  <svg
    viewBox="0 0 {width} {height}"
    width={width}
    height={height}
    role="img"
    aria-label={ariaLabel}
    class="overflow-visible"
  >
    <path d={areaPath} fill={stroke} fill-opacity="0.08" stroke="none" />
    <path d={path} fill="none" stroke={stroke} stroke-width={strokeWidth} stroke-linecap="round" stroke-linejoin="round" />
  </svg>
{:else}
  <div class="text-[10px] text-muted">no data</div>
{/if}
