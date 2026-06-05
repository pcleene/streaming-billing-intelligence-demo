<script lang="ts">
  import type { CurrentCycle } from "$lib/types";
  import { fmtMyr } from "$lib/utils";
  import { CalendarClock, AlertTriangle } from "lucide-svelte";

  interface Props { cycle: CurrentCycle }
  let { cycle }: Props = $props();

  const hasVariance = $derived(cycle.variance_myr != null && Math.abs(cycle.variance_myr) > 0.005);
  const varianceClass = $derived(
    cycle.variance_myr == null
      ? "text-muted"
      : cycle.variance_myr > 0
        ? "text-warn"
        : cycle.variance_myr < 0
          ? "text-ok"
          : "text-muted"
  );
</script>

<a class="card p-4 block hover:bg-elevated/40 transition-colors" href={`/bill-cycles/${cycle.cycle_id}`}>
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-2 text-sm font-medium">
      <CalendarClock size="14" class="text-accent" />
      Current bill cycle
    </div>
    <span class="font-mono text-[11px] text-muted">{cycle.cycle_id}</span>
  </div>
  <div class="mt-2 text-xs text-muted">
    {cycle.cycle_start} → {cycle.cycle_end} · <span class="text-fg/80">{cycle.days_remaining}d left</span>
  </div>
  <div class="mt-3 grid grid-cols-3 gap-2 text-xs">
    <div>
      <div class="text-muted">Expected</div>
      <div class="text-fg font-semibold">{fmtMyr(cycle.expected_amount_myr)}</div>
    </div>
    <div>
      <div class="text-muted">Billed</div>
      <div class="text-fg font-semibold">{cycle.billed_amount_myr == null ? "—" : fmtMyr(cycle.billed_amount_myr)}</div>
    </div>
    <div>
      <div class="text-muted">Variance</div>
      <div class={"font-semibold " + varianceClass}>
        {cycle.variance_myr == null ? "—" : fmtMyr(cycle.variance_myr)}
      </div>
    </div>
  </div>
  {#if hasVariance}
    <div class="mt-2 inline-flex items-center gap-1 text-[11px] text-warn">
      <AlertTriangle size="11" /> Variance detected — open cycle for drivers
    </div>
  {/if}
</a>
