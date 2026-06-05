<script lang="ts">
  import type { DriftImpact, DriftModelLineage } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import { fmtRelative } from "$lib/utils";
  import { ShieldAlert, AlertTriangle, GitBranch } from "lucide-svelte";

  interface Props {
    impact: DriftImpact | null;
    loading?: boolean;
    error?: string | null;
  }
  let { impact, loading = false, error = null }: Props = $props();

  /** Pull a normalized list of model versions out of either lineage shape. */
  function modelVersions(ml: DriftModelLineage | undefined): string[] {
    if (!ml) return [];
    if (Array.isArray(ml)) return ml;
    return Array.isArray(ml.models) ? ml.models : [];
  }

  /** Pull versions metadata if present in the dict shape. */
  function lineageMeta(ml: DriftModelLineage | undefined): {
    feature_set_version?: string;
    code_version?: string;
  } {
    if (!ml || Array.isArray(ml)) return {};
    return {
      feature_set_version: ml.feature_set_version,
      code_version: ml.code_version
    };
  }

  const ruleCount = $derived(impact?.blast_radius?.rule_count ?? 0);
  const modelCount = $derived(impact?.blast_radius?.model_count ?? 0);
  const versions = $derived(modelVersions(impact?.drift?.model_lineage));
  const meta = $derived(lineageMeta(impact?.drift?.model_lineage));
</script>

<div class="card p-5">
  <header class="mb-4 flex items-center gap-2">
    <ShieldAlert class="h-4 w-4 text-warn" />
    <h2 class="text-lg font-semibold">Downstream impact</h2>
  </header>

  {#if loading}
    <div class="space-y-3">
      <div class="grid-cards">
        <div class="card h-20 animate-pulse"></div>
        <div class="card h-20 animate-pulse"></div>
      </div>
      <div class="h-4 w-2/3 animate-pulse rounded bg-bg/60"></div>
      <div class="h-3 w-full animate-pulse rounded bg-bg/60"></div>
    </div>
  {:else if error}
    <div class="flex items-center gap-2 text-sm text-danger">
      <AlertTriangle class="h-4 w-4" />
      <span>{error}</span>
    </div>
  {:else if impact}
    <div class="space-y-4">
      <!-- Blast radius — only show as a big tile when non-zero. -->
      {#if ruleCount === 0 && modelCount === 0}
        <div class="rounded-md border border-border bg-bg/40 p-3 text-sm text-muted">
          No downstream rules or models consume this feature.
        </div>
      {:else}
        <div class="grid grid-cols-2 gap-3">
          {#if modelCount > 0}
            <KpiTile
              label="Models at risk"
              value={modelCount}
              accent="danger"
              sub="Models that read this feature"
            />
          {:else}
            <div class="rounded-md border border-border bg-bg/40 p-3">
              <div class="text-xs uppercase tracking-wide text-muted">Models at risk</div>
              <div class="mt-1 text-sm text-muted">No model consumers</div>
            </div>
          {/if}
          {#if ruleCount > 0}
            <KpiTile
              label="Rules at risk"
              value={ruleCount}
              accent="warn"
              sub="Rules that read this feature"
            />
          {:else}
            <div class="rounded-md border border-border bg-bg/40 p-3">
              <div class="text-xs uppercase tracking-wide text-muted">Rules at risk</div>
              <div class="mt-1 text-sm text-muted">No rule consumers</div>
            </div>
          {/if}
        </div>
      {/if}

      <!-- Affected consumers list -->
      <div>
        <div class="mb-2 text-xs uppercase tracking-wide text-muted">
          Affected consumers ({impact.affected_consumers?.length ?? 0})
        </div>
        {#if impact.affected_consumers && impact.affected_consumers.length > 0}
          <div class="overflow-hidden rounded-md border border-border">
            <table class="w-full text-sm">
              <thead class="bg-bg/60 text-left text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th class="px-3 py-2">Type</th>
                  <th class="px-3 py-2">Name</th>
                  <th class="px-3 py-2">Last seen</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-border">
                {#each impact.affected_consumers as c}
                  <tr>
                    <td class="px-3 py-2">
                      <span
                        class="rounded-full px-2 py-0.5 text-xs font-medium {c.type ===
                        'model'
                          ? 'bg-danger/15 text-danger'
                          : 'bg-accent/15 text-accent'}"
                      >
                        {c.type}
                      </span>
                    </td>
                    <td class="px-3 py-2 font-mono text-xs">{c.name}</td>
                    <td class="px-3 py-2 text-muted">{fmtRelative(c.last_seen)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else}
          <div class="text-sm text-muted">No downstream consumers known.</div>
        {/if}
      </div>

      <!-- Lineage / version metadata -->
      {#if versions.length > 0 || meta.feature_set_version || meta.code_version}
        <div class="rounded-md border border-border bg-bg/40 p-3">
          <div class="mb-1 flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted">
            <GitBranch class="h-3.5 w-3.5" />
            <span>Lineage</span>
          </div>
          {#if meta.feature_set_version || meta.code_version}
            <div class="text-xs text-fg">
              {#if meta.feature_set_version}
                <span class="text-muted">feature set</span>
                <span class="font-mono">{meta.feature_set_version}</span>
              {/if}
              {#if meta.feature_set_version && meta.code_version} · {/if}
              {#if meta.code_version}
                <span class="text-muted">code</span>
                <span class="font-mono">{meta.code_version}</span>
              {/if}
            </div>
          {/if}
          {#if versions.length > 0}
            <div class="mt-2 flex flex-wrap gap-1">
              {#each versions as v}
                <span class="pill pill-muted font-mono">{v}</span>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {:else}
    <div class="text-sm text-muted">No impact analysis available.</div>
  {/if}
</div>
