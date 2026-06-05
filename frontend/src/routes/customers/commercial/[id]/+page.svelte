<script lang="ts">
  import { page } from "$app/state";
  import { goto } from "$app/navigation";
  import { customersV3Api } from "$lib/api";
  import type { CustomerV3, UnifiedProfile } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import UnifiedProfilePanel from "$lib/components/profile-v3/UnifiedProfilePanel.svelte";
  import EntityPanelGrid from "$lib/components/profile-v3/EntityPanelGrid.svelte";
  import CrossEntityTrendsCard from "$lib/components/profile-v3/CrossEntityTrendsCard.svelte";
  import CurrentCycleWidget from "$lib/components/profile-v3/CurrentCycleWidget.svelte";
  import EquipmentInventory from "$lib/components/profile-v3/EquipmentInventory.svelte";
  import BrandJourneyTimeline from "$lib/components/profile-v3/BrandJourneyTimeline.svelte";
  import SupportInteractionsList from "$lib/components/profile-v3/SupportInteractionsList.svelte";
  import ActiveCampaignsTile from "$lib/components/profile-v3/ActiveCampaignsTile.svelte";
  import CommercialParentHeader from "$lib/components/commercial/CommercialParentHeader.svelte";
  import OutletRevenueDistribution from "$lib/components/commercial/OutletRevenueDistribution.svelte";
  import IForestScoreCard from "$lib/components/IForestScoreCard.svelte";
  import NextBestOfferCard from "$lib/components/NextBestOfferCard.svelte";
  import CustomerRefreshPanel from "$lib/components/CustomerRefreshPanel.svelte";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { fmtMyr, fmtRelative } from "$lib/utils";
  import { ArrowLeft, Sparkles, Receipt } from "lucide-svelte";

  const id = $derived(page.params.id);
  let view = $state<CustomerV3 | null>(null);
  let outlets = $state<CustomerV3[]>([]);
  let error = $state<string | null>(null);
  let loading = $state(false);

  // PR-15 retired `unified_profile` — identity scalars live at the
  // document root. Detect either the new flat-root shape or the legacy
  // V3 shape so cached responses from a pre-PR-15 backend still work.
  function isV3Shape(p: Partial<CustomerV3> | null): boolean {
    if (p == null || typeof p !== "object") return false;
    const typedAccount =
      p.customer_type === "residential" || p.customer_type === "commercial";
    if (!typedAccount) return false;
    const flatRoot = typeof p.name === "string" && Array.isArray(p.entities);
    const legacyV3 = p.unified_profile != null;
    return flatRoot || legacyV3;
  }

  // Build a synthetic UnifiedProfile from flat-root fields when the
  // backend has dropped `unified_profile` (PR-15+). Allows the existing
  // `UnifiedProfilePanel` component to keep working without a rewrite.
  // Return type kept as `UnifiedProfile | null` (instead of inferring
  // off `c.unified_profile`, which narrows away when `c` is null).
  function unifiedFromFlatRoot(c: CustomerV3 | null): UnifiedProfile | null {
    if (!c) return null;
    if (c.unified_profile) return c.unified_profile;
    if (!c.contact || !c.address) return null;
    return {
      name:           c.name ?? c.customer_id,
      preferred_name: c.preferred_name ?? undefined,
      ic_number:      c.ic_number ?? undefined,
      date_of_birth:  c.date_of_birth ?? undefined,
      ethnicity:      c.ethnicity ?? undefined,
      gender:         c.gender ?? undefined,
      contact:        c.contact,
      address:        c.address,
    };
  }

  async function load() {
    if (!id) return;
    loading = true;
    error = null;
    try {
      let candidate: CustomerV3 | null = null;
      try {
        candidate = await customersV3Api.commercial(id, { inspect: inspector.open });
      } catch {
        candidate = await customersV3Api.profile(id, { inspect: inspector.open });
      }
      if (candidate && candidate.customer_type === "residential") {
        await goto(`/customers/residential/${id}`, { replaceState: true });
        return;
      }
      if (!isV3Shape(candidate as Partial<CustomerV3>)) {
        // Backend still on V2 — fall back to legacy SCV page so the user
        // gets useful data instead of a crashed component tree.
        await goto(`/customers/${id}?legacy=1`, { replaceState: true });
        return;
      }
      view = candidate;
      if (view && (view.is_parent_account || !view.parent_account_id)) {
        try {
          const o = await customersV3Api.outlets(view.customer_id);
          outlets = o.items ?? [];
        } catch {
          // Endpoint may not be served yet — leave outlets empty, render gracefully.
          outlets = [];
        }
      } else {
        outlets = [];
      }
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "load failed";
      view = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
  $effect(() => { if (inspector.open) load(); });

  const isParent = $derived(view?.is_parent_account === true || (view != null && !view.parent_account_id));
  const displayName = $derived(view?.name ?? view?.unified_profile?.name ?? id);
  const unified = $derived(unifiedFromFlatRoot(view));
</script>

<div class="space-y-6">
  {#if error}<div class="card p-3 text-sm text-danger">{error}</div>{/if}

  {#if view}
    {#if !isParent && view.parent_account_id}
      <a class="inline-flex items-center gap-1 text-xs text-accent hover:underline" href={`/customers/commercial/${view.parent_account_id}`}>
        <ArrowLeft size="12" /> Back to parent account
      </a>
    {/if}

    {#if isParent}
      <div class="flex items-start justify-between gap-3">
        <CommercialParentHeader customer={view} />
        <InspectorTrigger hint="customers_commercial.find_one" />
      </div>
    {:else}
      <header>
        <div class="text-xs text-muted">Commercial outlet</div>
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="mt-1 text-2xl font-semibold">
            {displayName}
            <span class="ml-2 text-sm font-normal text-muted">{id}</span>
          </h1>
          <InspectorTrigger hint="customers_commercial.find_one" />
        </div>
      </header>
    {/if}

    <UnifiedProfilePanel profile={unified} customerId={view.customer_id} />

    <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <KpiTile label="Total LTV" value={fmtMyr(view.cross_entity_metrics?.total_ltv_myr ?? 0)} accent="accent" />
      <KpiTile label="Tier" value={view.tier} />
      <KpiTile
        label="Churn risk"
        value={`${Math.round((view.cross_entity_metrics?.churn_risk ?? 0) * 100)}%`}
        accent={(view.cross_entity_metrics?.churn_risk ?? 0) > 0.5 ? "danger" : (view.cross_entity_metrics?.churn_risk ?? 0) > 0.25 ? "warn" : "ok"}
      />
      <KpiTile label="Open cases" value={view.open_cases?.length ?? 0}
               accent={(view.open_cases?.length ?? 0) > 0 ? "warn" : "ok"} />
    </div>

    {#if view.current_cycle}
      <CurrentCycleWidget cycle={view.current_cycle} />
    {/if}

    {#if isParent && outlets.length > 0}
      <OutletRevenueDistribution outlets={outlets} parentId={view.customer_id} />
    {/if}

    <Section title="Service portfolio" subtitle={`${view.entities?.length ?? 0} entities`}>
      <EntityPanelGrid entities={view.entities ?? []} profiles={view.entity_profiles ?? {}} />
    </Section>

    {#if view.cross_entity_metrics}
      <CrossEntityTrendsCard metrics={view.cross_entity_metrics} />
    {/if}

    {#if (view.brand_journey?.length ?? 0) > 0 || (view.active_campaigns?.length ?? 0) > 0}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BrandJourneyTimeline events={view.brand_journey ?? []} />
        <ActiveCampaignsTile campaigns={view.active_campaigns ?? []} />
      </div>
    {/if}

    {#if (view.interaction_history?.support_interactions?.length ?? 0) > 0 || (view.recent_transactions?.length ?? 0) > 0}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {#if (view.recent_transactions?.length ?? 0) > 0}
          <Section title="Recent transactions" subtitle={`Last ${Math.min(view.recent_transactions?.length ?? 0, 10)} accepted events`}>
            {#snippet actions()}
              <Receipt size="14" class="text-muted" />
            {/snippet}
            <div class="overflow-hidden rounded-lg border border-border">
              <table class="w-full text-left text-sm">
                <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th class="px-3 py-2">When</th>
                    <th class="px-3 py-2">Type</th>
                    <th class="px-3 py-2 text-right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {#each (view.recent_transactions ?? []).slice(0, 10) as t}
                    <tr class="border-t border-border">
                      <td class="px-3 py-2 text-xs text-muted">{fmtRelative(t.timestamp)}</td>
                      <td class="px-3 py-2 text-xs"><span class="pill pill-muted text-[10px]">{t.transaction_type ?? "—"}</span></td>
                      <td class="px-3 py-2 text-right tabular-nums">{fmtMyr(t.amount)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </Section>
        {/if}
        <SupportInteractionsList interactions={view.interaction_history?.support_interactions ?? []} />
      </div>
    {/if}

    <EquipmentInventory equipment={view.equipment ?? []} />

    {#if view.embed_source?.text}
      <Section
        title="Semantic profile signature"
        subtitle="AutoEmbed source — what /customers/search ranks against."
      >
        {#snippet actions()}
          <span class="pill pill-accent text-[10px] inline-flex items-center gap-1">
            <Sparkles size="11" /> AutoEmbed
          </span>
        {/snippet}
        <pre class="whitespace-pre-wrap break-words rounded-md border border-border bg-elevated/40 p-3 text-xs leading-relaxed text-fg/90 font-mono line-clamp-4">{view.embed_source.text}</pre>
      </Section>
    {/if}

    {#if view.open_cases && view.open_cases.length > 0}
      <Section title="Open cases" subtitle={`${view.open_cases.length}`}>
        <ul class="divide-y divide-border">
          {#each view.open_cases as c}
            <li class="flex items-center gap-3 py-2 text-sm">
              <SeverityBadge severity={c.severity} />
              <a class="font-mono text-accent hover:underline" href={`/quarantine/${c.case_id}`}>{c.case_id}</a>
              <span class="text-muted">{fmtRelative(c.created_at)}</span>
              <span class="ml-auto">{fmtMyr(c.amount)}</span>
            </li>
          {/each}
        </ul>
      </Section>
    {/if}

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <NextBestOfferCard customerId={view.customer_id} initial={view.recommendations ?? null} />
      <IForestScoreCard customerId={view.customer_id} />
    </div>

    <CustomerRefreshPanel customerId={view.customer_id} />

  {:else if loading}
    <div class="text-sm text-muted">Loading commercial account…</div>
  {:else if !error}
    <div class="text-sm text-muted">No account.</div>
  {/if}
</div>
