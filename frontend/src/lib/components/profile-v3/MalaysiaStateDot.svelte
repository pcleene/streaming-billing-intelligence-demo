<script lang="ts">
  // Tiny inline preview that just dots the customer's state on a
  // schematic Malaysia. The full SVG lives at $lib/maps/malaysia.svg
  // but we keep this version inline so the marker positioning stays
  // close to the data. Coordinates are approximate centroids in the
  // 0..200 (peninsula) and 200..420 (East Malaysia) viewBox.
  interface Props { state: string }
  let { state }: Props = $props();

  // viewBox 0 0 440 200
  const CENTROIDS: Record<string, [number, number]> = {
    perlis: [38, 22],
    kedah: [55, 42],
    "pulau pinang": [42, 58],
    penang: [42, 58],
    perak: [70, 78],
    selangor: [80, 110],
    "kuala lumpur": [85, 112],
    "wp kuala lumpur": [85, 112],
    putrajaya: [88, 118],
    "negeri sembilan": [98, 128],
    melaka: [104, 142],
    malacca: [104, 142],
    johor: [128, 158],
    pahang: [120, 100],
    terengganu: [142, 70],
    kelantan: [110, 50],
    sabah: [350, 70],
    sarawak: [270, 130],
    labuan: [322, 78]
  };

  const key = $derived(state?.toLowerCase().trim() ?? "");
  const pt = $derived(CENTROIDS[key] ?? null);
</script>

<svg viewBox="0 0 440 200" width="120" height="56" role="img" aria-label="Malaysia map" class="text-muted/60">
  <!-- Peninsular Malaysia silhouette (rough) -->
  <path
    d="M40 16 L70 18 L88 38 L96 70 L108 95 L114 120 L132 142 L138 160 L120 168 L96 162 L80 150 L70 130 L60 108 L52 92 L46 70 L40 50 Z"
    fill="currentColor" fill-opacity="0.15" stroke="currentColor" stroke-opacity="0.4" stroke-width="1"
  />
  <!-- Sarawak -->
  <path
    d="M210 110 L260 100 L300 110 L320 130 L300 150 L260 158 L220 152 L200 138 Z"
    fill="currentColor" fill-opacity="0.15" stroke="currentColor" stroke-opacity="0.4" stroke-width="1"
  />
  <!-- Sabah -->
  <path
    d="M330 50 L370 48 L388 70 L386 92 L362 102 L344 96 L328 78 Z"
    fill="currentColor" fill-opacity="0.15" stroke="currentColor" stroke-opacity="0.4" stroke-width="1"
  />

  {#if pt}
    <circle cx={pt[0]} cy={pt[1]} r="6" fill="rgb(236 72 175)" fill-opacity="0.25" />
    <circle cx={pt[0]} cy={pt[1]} r="2.5" fill="rgb(236 72 175)" />
  {/if}
</svg>

{#if state}
  <div class="mt-1 text-center text-[10px] text-muted">{state}</div>
{/if}
