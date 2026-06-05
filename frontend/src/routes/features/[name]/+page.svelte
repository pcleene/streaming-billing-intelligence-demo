<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import { driftApi, driftHistoryApi } from "$lib/api";
  import type { DriftHistoryPoint } from "$lib/api";
  import type { DriftStatus, DriftImpact } from "$lib/types";
  import DriftStatusCard from "$lib/components/DriftStatusCard.svelte";
  import ImpactAnalysisPanel from "$lib/components/ImpactAnalysisPanel.svelte";
  import DistributionShiftPanel from "$lib/components/DistributionShiftPanel.svelte";
  import InvestigateActionForm from "$lib/components/InvestigateActionForm.svelte";
  import DriftHistoryChart from "$lib/components/DriftHistoryChart.svelte";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { ArrowLeft } from "lucide-svelte";

  const featureName = $derived(page.params.name ?? "");

  let status = $state<DriftStatus | null>(null);
  let statusLoading = $state(true);
  let statusErr = $state<string | null>(null);

  let impact = $state<DriftImpact | null>(null);
  let impactLoading = $state(true);
  let impactErr = $state<string | null>(null);

  let history = $state<DriftHistoryPoint[]>([]);
  let historyErr = $state<string | null>(null);

  async function loadHistory() {
    try {
      const r = await driftHistoryApi.history(featureName, 30);
      history = r.items ?? [];
      historyErr = null;
    } catch (e) {
      historyErr = e instanceof Error ? e.message : String(e);
      history = [];
    }
  }

  async function loadStatus() {
    statusLoading = true;
    statusErr = null;
    try {
      status = await driftApi.driftStatus(featureName);
    } catch (e) {
      statusErr = e instanceof Error ? e.message : String(e);
      status = null;
    } finally {
      statusLoading = false;
    }
  }

  async function loadImpact() {
    impactLoading = true;
    impactErr = null;
    try {
      impact = await driftApi.impactAnalysis(featureName);
    } catch (e) {
      impactErr = e instanceof Error ? e.message : String(e);
      impact = null;
    } finally {
      impactLoading = false;
    }
  }

  onMount(() => {
    loadStatus();
    loadImpact();
    loadHistory();
  });
</script>

<div class="space-y-6">
  <header class="flex items-start justify-between gap-4">
    <div>
      <a
        href="/features"
        class="mb-2 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"
      >
        <ArrowLeft class="h-3.5 w-3.5" />
        Back to features
      </a>
      <div class="flex items-center gap-2">
        <h1 class="text-2xl font-semibold">
          Feature: <span class="font-mono">{featureName}</span>
        </h1>
        <InspectorTrigger hint="feature_drift_metrics.find_one" />
      </div>
      <p class="text-sm text-muted">
        Drift telemetry, downstream impact and investigate actions.
      </p>
    </div>
  </header>

  <div class="grid gap-6 lg:grid-cols-2">
    <DriftStatusCard {status} loading={statusLoading} error={statusErr} />
    <ImpactAnalysisPanel {impact} loading={impactLoading} error={impactErr} />
  </div>

  <DistributionShiftPanel
    current={status?.current}
    baseline={status?.baseline}
    featureName={featureName}
    loading={statusLoading}
  />

  <DriftHistoryChart points={history} />
  {#if historyErr}
    <p class="text-xs text-danger">Could not load drift history: {historyErr}</p>
  {:else if history.length === 0}
    <p class="text-xs text-muted">No measurements in the last 30 days.</p>
  {/if}

  <InvestigateActionForm {featureName} />
</div>
