<script lang="ts">
  import { page } from "$app/state";
  import { goto } from "$app/navigation";
  import { customersV3Api } from "$lib/api";
  import type { CustomerV3 } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import NextBestOfferCard from "$lib/components/NextBestOfferCard.svelte";
  import EntityPanelGrid from "$lib/components/profile-v3/EntityPanelGrid.svelte";
  import CrossEntityTrendsCard from "$lib/components/profile-v3/CrossEntityTrendsCard.svelte";
  import BrandJourneyTimeline from "$lib/components/profile-v3/BrandJourneyTimeline.svelte";
  import SupportInteractionsList from "$lib/components/profile-v3/SupportInteractionsList.svelte";
  import MarketingFunnelTile from "$lib/components/profile-v3/MarketingFunnelTile.svelte";
  import ChannelEngagementBars from "$lib/components/profile-v3/ChannelEngagementBars.svelte";
  import ActiveCampaignsTile from "$lib/components/profile-v3/ActiveCampaignsTile.svelte";
  import EquipmentInventory from "$lib/components/profile-v3/EquipmentInventory.svelte";
  import CurrentCycleWidget from "$lib/components/profile-v3/CurrentCycleWidget.svelte";
  import MalaysiaStateDot from "$lib/components/profile-v3/MalaysiaStateDot.svelte";
  import IForestScoreCard from "$lib/components/IForestScoreCard.svelte";
  import CustomerRefreshPanel from "$lib/components/CustomerRefreshPanel.svelte";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { fmtMyr, fmtRelative, fmtDate } from "$lib/utils";
  import { Mail, Phone, MapPin, ArrowLeft, Sparkles, IdCard, Users, Receipt, MessageSquare } from "lucide-svelte";

  const id = $derived(page.params.id);
  let view = $state<CustomerV3 | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(false);

  async function load() {
    if (!id) return;
    loading = true;
    error = null;
    try {
      let candidate: CustomerV3 | null = null;
      try {
        candidate = await customersV3Api.residential(id, { inspect: inspector.open });
      } catch {
        candidate = await customersV3Api.profile(id, { inspect: inspector.open });
      }
      if (candidate?.customer_type === "commercial") {
        await goto(`/customers/commercial/${id}`, { replaceState: true });
        return;
      }
      view = candidate;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "load failed";
      view = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
  $effect(() => { if (inspector.open) load(); });

  const tierAccent = $derived(
    view?.tier === "platinum" ? "accent" :
    view?.tier === "gold" ? "warn" :
    view?.tier === "silver" ? "default" : "default"
  );

  // Flat-root V3 derives — name & contact live on the doc root post-PR-15.
  // We fall back to the deprecated `unified_profile` block in case a
  // stale backend still serves the PR-14 shape.
  const displayName = $derived(view?.name ?? view?.unified_profile?.name ?? id);
  const contact = $derived(view?.contact ?? view?.unified_profile?.contact ?? null);
  const address = $derived(view?.address ?? view?.unified_profile?.address ?? null);

  const churnPct = $derived(
    Math.round(((view?.cross_entity_metrics?.churn_risk ?? 0) as number) * 100)
  );
  const churnAccent = $derived(
    churnPct > 50 ? "danger" : churnPct > 25 ? "warn" : "ok"
  );

  const subTotalMyr = $derived(
    (view?.subscriptions ?? []).reduce(
      (a, s) => a + (s.monthly_fee_myr ?? 0), 0
    )
  );

  const embedText = $derived(view?.embed_source?.text ?? "");
  let signatureExpanded = $state(false);

  function tierTint(t: string | undefined): string {
    switch (t) {
      case "platinum": return "pill-accent";
      case "gold": return "pill-warn";
      case "silver": return "pill-muted";
      default: return "pill-muted";
    }
  }
</script>

<div class="space-y-6">
  <a
    href="/customers/search"
    class="inline-flex items-center gap-1 text-xs text-muted hover:text-fg transition-colors"
  >
    <ArrowLeft size="12" />
    Back to customer search
  </a>

  {#if error}
    <div class="card p-3 text-sm text-danger">{error}</div>
  {/if}

  {#if view}
    <!-- ============ HERO ============ -->
    <section class="card-elevated p-6">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted">
            <span>Residential customer</span>
            <span>·</span>
            <InspectorTrigger hint="customers_residential.find_one" />
          </div>
          <h1 class="mt-2 text-3xl font-semibold tracking-tight">{displayName}</h1>
          <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <span class="font-mono text-muted">{view.customer_id}</span>
            {#if view.account_id}
              <span class="text-muted">·</span>
              <span class="font-mono text-muted" title="Account ID">{view.account_id}</span>
            {/if}
            {#if view.ic_number}
              <span class="text-muted">·</span>
              <span class="inline-flex items-center gap-1 font-mono text-muted">
                <IdCard size="11" /> {view.ic_number}
              </span>
            {/if}
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-2">
            <span class={`pill ${tierTint(view.tier)} uppercase text-[10px] font-semibold`}>
              {view.tier ?? "—"} tier
            </span>
            {#each view.entities ?? [] as e}
              <span class="pill pill-accent text-[10px]">{e.replace("acme_", "")}</span>
            {/each}
            {#if view.iforest_score != null}
              <span
                class="pill pill-muted text-[10px]"
                title="Isolation-forest anomaly score (higher = more anomalous)"
              >
                iForest {view.iforest_score.toFixed(2)}
              </span>
            {/if}
            {#if (view.lifetime_quarantine_count ?? 0) > 0}
              <span class="pill pill-warn text-[10px]">
                {view.lifetime_quarantine_count} lifetime quarantines
              </span>
            {/if}
          </div>

          <!-- Contact + address strip -->
          <div class="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
            {#if contact?.email}
              <div class="flex items-center gap-2 text-muted">
                <Mail size="14" />
                <span class="truncate font-mono text-fg/90">{contact.email}</span>
              </div>
            {/if}
            {#if contact?.phone}
              <div class="flex items-center gap-2 text-muted">
                <Phone size="14" />
                <span class="font-mono text-fg/90">{contact.phone}</span>
              </div>
            {/if}
            {#if address?.state}
              <div class="flex items-start gap-2 text-muted">
                <MapPin size="14" class="mt-0.5" />
                <span class="text-fg/90">
                  {[address.city, address.state].filter(Boolean).join(", ")}
                </span>
              </div>
            {/if}
            {#if view.household_size}
              <div class="flex items-center gap-2 text-muted">
                <Users size="14" />
                <span class="text-fg/90">Household of {view.household_size}</span>
              </div>
            {/if}
          </div>
        </div>

        {#if address?.state}
          <div class="shrink-0">
            <MalaysiaStateDot state={address.state} />
          </div>
        {/if}
      </div>
    </section>

    <!-- ============ KPIs ============ -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <KpiTile
        label="Total LTV"
        value={fmtMyr(view.cross_entity_metrics?.total_ltv_myr ?? 0)}
        sub={view.cross_entity_metrics?.ltv_band ? `band: ${view.cross_entity_metrics.ltv_band}` : ""}
        accent={tierAccent}
      />
      <KpiTile
        label="Monthly spend"
        value={fmtMyr(subTotalMyr || (view.total_monthly_value_myr ?? 0))}
        sub={`${view.subscriptions?.length ?? 0} active sub${(view.subscriptions?.length ?? 0) === 1 ? "" : "s"}`}
      />
      <KpiTile
        label="Churn risk"
        value={`${churnPct}%`}
        sub={view.cross_entity_metrics?.churn_risk_band as string | undefined}
        accent={churnAccent}
      />
      <KpiTile
        label="Open cases"
        value={view.open_cases?.length ?? 0}
        accent={(view.open_cases?.length ?? 0) > 0 ? "warn" : "ok"}
      />
    </div>

    <!-- ============ Semantic signature (AutoEmbed source text) ============ -->
    {#if embedText}
      <Section
        title="Semantic profile signature"
        subtitle="The text Atlas Vector Search embeds for this customer (voyage-4-large)."
      >
        {#snippet actions()}
          <div class="flex items-center gap-2">
            <span class="pill pill-accent text-[10px] inline-flex items-center gap-1">
              <Sparkles size="11" /> AutoEmbed
            </span>
            <button
              type="button"
              class="text-xs text-accent hover:underline"
              onclick={() => (signatureExpanded = !signatureExpanded)}
            >
              {signatureExpanded ? "Collapse" : "Expand"}
            </button>
          </div>
        {/snippet}
        <pre
          class="whitespace-pre-wrap break-words rounded-md border border-border bg-elevated/40 p-3 text-xs leading-relaxed text-fg/90 font-mono {signatureExpanded ? '' : 'line-clamp-4'}"
        >{embedText}</pre>
        <p class="mt-2 text-[11px] text-muted">
          This string is what natural-language queries on
          <a href="/customers/search" class="text-accent hover:underline">/customers/search</a>
          rank against. The vector itself never leaves Atlas.
        </p>
      </Section>
    {/if}

    <!-- ============ Current cycle ============ -->
    {#if view.current_cycle}
      <CurrentCycleWidget cycle={view.current_cycle} />
    {/if}

    <!-- ============ Service portfolio ============ -->
    <Section title="Service portfolio" subtitle={`${view.entities?.length ?? 0} Acme entities`}>
      <EntityPanelGrid entities={view.entities ?? []} profiles={view.entity_profiles ?? {}} />
    </Section>

    <!-- ============ Subscriptions / promotions / entitlements ============ -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Section title="Subscriptions" subtitle={`${view.subscriptions?.length ?? 0} active`}>
        {#if (view.subscriptions?.length ?? 0) === 0}
          <p class="text-sm text-muted">No active subscriptions.</p>
        {:else}
          <ul class="divide-y divide-border text-sm">
            {#each view.subscriptions ?? [] as s}
              <li class="flex items-center justify-between py-2">
                <div class="min-w-0">
                  <div class="truncate">{s.package_name ?? s.package_code}</div>
                  {#if s.next_billing_at}
                    <div class="text-[11px] text-muted">
                      next billing {fmtRelative(s.next_billing_at)}
                    </div>
                  {/if}
                </div>
                <div class="text-right font-mono text-muted">{fmtMyr(s.monthly_fee_myr ?? 0)}</div>
              </li>
            {/each}
          </ul>
        {/if}
      </Section>

      <Section title="Active promotions" subtitle={`${view.active_promotions?.length ?? 0}`}>
        {#if (view.active_promotions?.length ?? 0) === 0}
          <p class="text-sm text-muted">
            No active promotion. Bills carrying a discount trigger quarantine review.
          </p>
        {:else}
          <ul class="space-y-2 text-sm">
            {#each view.active_promotions ?? [] as p}
              <li class="rounded-md border border-border bg-elevated/40 p-2.5">
                <div class="flex items-center justify-between gap-2">
                  <span class="font-mono text-xs">{p.promotion_code}</span>
                  {#if p.discount_amount_myr != null}
                    <span class="pill pill-accent text-[10px]">−{fmtMyr(p.discount_amount_myr)}</span>
                  {:else if p.discount_pct != null}
                    <span class="pill pill-accent text-[10px]">−{Math.round(p.discount_pct * 100)}%</span>
                  {/if}
                </div>
                {#if p.description}
                  <p class="mt-1 text-xs text-muted">{p.description}</p>
                {/if}
                {#if p.valid_to}
                  <p class="mt-1 text-[11px] text-muted">until {fmtRelative(p.valid_to)}</p>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </Section>

      <Section title="Entitlements" subtitle={`${view.entitlements?.length ?? 0}`}>
        {#if (view.entitlements?.length ?? 0) === 0}
          <p class="text-sm text-muted">No PPV / add-on entitlements.</p>
        {:else}
          <ul class="space-y-1 text-sm">
            {#each view.entitlements ?? [] as e}
              <li class="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-elevated/40 px-2 py-1.5">
                <span class="truncate text-xs">{e.content_name ?? e.content_id}</span>
                {#if e.expires_at}
                  <span class="text-[10px] text-muted">{fmtRelative(e.expires_at)}</span>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </Section>
    </div>

    <!-- ============ Cross-entity trends ============ -->
    {#if view.cross_entity_metrics}
      <CrossEntityTrendsCard metrics={view.cross_entity_metrics} />
    {/if}

    <!-- ============ Journey + campaigns ============ -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <BrandJourneyTimeline events={view.brand_journey ?? []} />
      <ActiveCampaignsTile campaigns={view.active_campaigns ?? []} />
    </div>

    <!-- ============ Support + marketing ============ -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <SupportInteractionsList
        interactions={view.interaction_history?.support_interactions ?? []}
      />
      <div class="space-y-4">
        <MarketingFunnelTile
          interactions={view.interaction_history?.marketing_interactions ?? []}
        />
        <ChannelEngagementBars
          rates={view.interaction_history?.channel_engagement_rates ?? {}}
        />
      </div>
    </div>

    <EquipmentInventory equipment={view.equipment ?? []} />

    <!-- ============ Recent activity (transactions + support) ============ -->
    {#if (view.recent_transactions?.length ?? 0) > 0 || (view.recent_support?.length ?? 0) > 0}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Section
          title="Recent transactions"
          subtitle={`Last ${Math.min(view.recent_transactions?.length ?? 0, 10)} accepted events`}
        >
          {#snippet actions()}
            <Receipt size="14" class="text-muted" />
          {/snippet}
          {#if (view.recent_transactions?.length ?? 0) === 0}
            <p class="text-sm text-muted">No recent transactions on this customer.</p>
          {:else}
            <div class="overflow-hidden rounded-lg border border-border">
              <table class="w-full text-left text-sm">
                <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                  <tr>
                    <th class="px-3 py-2">When</th>
                    <th class="px-3 py-2">Type</th>
                    <th class="px-3 py-2 text-right">Amount</th>
                    <th class="px-3 py-2">Txn</th>
                  </tr>
                </thead>
                <tbody>
                  {#each (view.recent_transactions ?? []).slice(0, 10) as t}
                    <tr class="border-t border-border">
                      <td class="px-3 py-2 text-xs text-muted">{fmtRelative(t.timestamp)}</td>
                      <td class="px-3 py-2 text-xs">
                        <span class="pill pill-muted text-[10px]">{t.transaction_type ?? "—"}</span>
                      </td>
                      <td class="px-3 py-2 text-right tabular-nums">{fmtMyr(t.amount)}</td>
                      <td class="px-3 py-2 font-mono text-[10px] text-fg/70">{(t.transaction_id ?? "").slice(0, 16)}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </Section>

        <Section
          title="Recent support summary"
          subtitle="Latest tickets with sentiment"
        >
          {#snippet actions()}
            <MessageSquare size="14" class="text-muted" />
          {/snippet}
          {#if (view.recent_support?.length ?? 0) === 0}
            <p class="text-sm text-muted">No recent support tickets.</p>
          {:else}
            <ul class="space-y-2 text-sm">
              {#each (view.recent_support ?? []).slice(0, 8) as t}
                {@const tone = t.sentiment === "negative" ? "pill-warn"
                  : t.sentiment === "positive" ? "pill-accent" : "pill-muted"}
                <li class="rounded-md border border-border/60 bg-elevated/40 p-2.5">
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-mono text-[11px] text-fg/80">{t.ticket_id}</span>
                    {#if t.sentiment}
                      <span class="pill {tone} text-[10px]">{t.sentiment}</span>
                    {/if}
                  </div>
                  <p class="mt-1 text-xs text-fg/90 line-clamp-2">{t.summary}</p>
                  <div class="mt-1 flex items-center gap-3 text-[11px] text-muted">
                    {#if t.opened_at}<span>opened {fmtRelative(t.opened_at)}</span>{/if}
                    {#if t.closed_at}<span>· closed {fmtRelative(t.closed_at)}</span>{/if}
                  </div>
                </li>
              {/each}
            </ul>
          {/if}
        </Section>
      </div>
    {/if}

    <!-- ============ Open cases ============ -->
    {#if view.open_cases && view.open_cases.length > 0}
      <Section title="Open quarantine cases" subtitle={`${view.open_cases.length}`}>
        <ul class="divide-y divide-border">
          {#each view.open_cases as c}
            <li class="flex items-center gap-3 py-2 text-sm">
              <SeverityBadge severity={c.severity} />
              <a
                class="font-mono text-accent hover:underline"
                href={`/quarantine/${c.case_id}`}
              >
                {c.case_id}
              </a>
              <span class="text-muted">{fmtRelative(c.created_at)}</span>
              <span class="ml-auto">{fmtMyr(c.amount)}</span>
            </li>
          {/each}
        </ul>
      </Section>
    {/if}

    <!-- ============ Recommendations + iForest ============ -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <NextBestOfferCard
        customerId={view.customer_id}
        initial={view.recommendations ?? null}
      />
      <IForestScoreCard customerId={view.customer_id} />
    </div>

    <!-- ============ Customer 360 / embedding refresh ============ -->
    <CustomerRefreshPanel customerId={view.customer_id} />

  {:else if loading}
    <div class="flex items-center gap-2 text-sm text-muted">
      <span class="inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
      Loading customer profile…
    </div>
  {:else if !error}
    <div class="text-sm text-muted">No customer.</div>
  {/if}
</div>
