<script lang="ts">
  import { page } from "$app/state";
  import { goto } from "$app/navigation";
  import { customersApi, customersV3Api } from "$lib/api";
  import type { Customer360, CustomerV3 } from "$lib/types";
  import KpiTile from "$lib/components/KpiTile.svelte";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import JsonView from "$lib/components/JsonView.svelte";
  import NextBestOfferCard from "$lib/components/NextBestOfferCard.svelte";
  import CustomerRefreshPanel from "$lib/components/CustomerRefreshPanel.svelte";
  import TransactionPatternPanel from "$lib/components/TransactionPatternPanel.svelte";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { fmtMyr, fmtRelative, fmtDate } from "$lib/utils";

  const id = $derived(page.params.id);
  let crmLag = $state(false);
  let view = $state<Customer360 | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(false);

  // When the user toggles CRM-lag simulation we stay on this legacy page
  // (the V2 endpoint is the only one that honours that flag). Otherwise
  // we dispatch to /customers/residential/[id] or /customers/commercial/[id]
  // — but ONLY when the response actually has V3 shape. The backend may
  // still be serving the V2 payload from this endpoint; in that case we
  // render the legacy view inline.
  //
  // PR-15 retired `unified_profile` and lifted identity scalars to the
  // document root, so the post-PR-15 shape detection now keys off the
  // flat-root `name` + `tier` + `entities` triple. The legacy
  // `unified_profile` check is kept as a fallback so cached responses
  // from a pre-PR-15 backend still dispatch correctly.
  function isV3Shape(p: Partial<CustomerV3>): boolean {
    if (p == null || typeof p !== "object") return false;
    const typedAccount =
      p.customer_type === "residential" || p.customer_type === "commercial";
    if (!typedAccount) return false;
    const flatRoot = typeof p.name === "string" && Array.isArray(p.entities);
    const legacyV3 = p.unified_profile != null;
    return flatRoot || legacyV3;
  }

  async function tryDispatch(): Promise<boolean> {
    if (!id || crmLag) return false;
    try {
      const v3 = await customersV3Api.profile(id, { inspect: inspector.open }) as Partial<CustomerV3>;
      if (!isV3Shape(v3)) return false;
      const target = v3.customer_type === "commercial"
        ? `/customers/commercial/${id}`
        : `/customers/residential/${id}`;
      await goto(target, { replaceState: true });
      return true;
    } catch {
      // V3 endpoint not present yet — fall back to the V2 view below.
      return false;
    }
  }

  async function load() {
    if (!id) return;
    loading = true;
    error = null;
    try {
      if (await tryDispatch()) return;
      view = await customersApi.get(id, crmLag);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "load failed";
      view = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
  // Refetch with inspect=true when the panel opens.
  $effect(() => { if (inspector.open) load(); });

  const subTotal = $derived(
    view?.subscriptions?.reduce((a, s) => a + (s.monthly_price_myr ?? 0), 0) ?? 0
  );
</script>

<div class="space-y-6">
  <header class="flex items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-2">
        <h1 class="text-2xl font-semibold">
          {view?.name ?? id}
          <span class="ml-3 text-sm font-normal text-muted">{id}</span>
        </h1>
        <InspectorTrigger hint="customers.find_one" />
      </div>
      <p class="text-sm text-muted">Single Customer View — consolidated profile, transactions, cases, features.</p>
    </div>
    <label class="flex items-center gap-2 text-sm">
      <input type="checkbox" bind:checked={crmLag} onchange={load} />
      <span class="text-muted">Simulate CRM lag</span>
    </label>
  </header>

  {#if error}<div class="card p-3 text-sm text-danger">{error}</div>{/if}

  {#if view}
    {#if view.crm_lag?.simulated}
      <div class="card-elevated border-warn/40 p-3 text-xs text-warn">
        Showing a simulated CRM warehouse snapshot from {fmtDate(view.crm_lag.snapshot_at)}
        ({view.crm_lag.lag_hours}h lag). Live operational data is hidden.
      </div>
    {/if}

    <div class="grid-cards">
      <KpiTile label="Segment" value={view.segment} accent="accent" />
      <KpiTile label="State" value={view.address?.state ?? "—"} />
      <KpiTile label="Active subs" value={view.subscriptions?.length ?? 0} sub={fmtMyr(subTotal) + ' / mo'} />
      <KpiTile label="Lifetime quarantines" value={view.lifetime_quarantine_count ?? 0}
               accent={(view.lifetime_quarantine_count ?? 0) > 0 ? 'warn' : 'ok'} />
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Section title="Profile" subtitle={view.email}>
        <dl class="space-y-2 text-sm">
          <div class="flex justify-between"><dt class="text-muted">Phone</dt><dd>{view.phone ?? '—'}</dd></div>
          <div class="flex justify-between"><dt class="text-muted">City</dt><dd>{view.address?.city ?? '—'}</dd></div>
          <div class="flex justify-between"><dt class="text-muted">Postcode</dt><dd>{view.address?.postcode ?? '—'}</dd></div>
        </dl>
      </Section>

      <Section title="Subscriptions" subtitle={`${view.subscriptions?.length ?? 0} active`}>
        <ul class="space-y-2 text-sm">
          {#each view.subscriptions ?? [] as s}
            <li class="flex justify-between">
              <span>{s.package_name}</span>
              <span class="text-muted">{fmtMyr(s.monthly_price_myr)}</span>
            </li>
          {/each}
        </ul>
      </Section>

      <Section title="Active promotions" subtitle={`${view.active_promotions?.length ?? 0}`}>
        {#if (view.active_promotions?.length ?? 0) === 0}
          <p class="text-sm text-muted">No active promotion. Discounts trigger quarantine.</p>
        {:else}
          <ul class="space-y-2 text-sm">
            {#each view.active_promotions ?? [] as p}
              <li class="flex justify-between">
                <span class="font-mono">{p.promo_code}</span>
                <span class="text-muted">{Math.round(p.discount_pct * 100)}% — until {fmtDate(p.valid_until)}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </Section>
    </div>

    <Section title="Recent transactions" subtitle="Last 50, ASP-fed">
      {#if (view.recent_transactions_full?.length ?? 0) === 0}
        <p class="text-sm text-muted">No transactions in window.</p>
      {:else}
        <div class="overflow-hidden rounded-lg border border-border">
          <table class="w-full text-left text-xs">
            <thead class="bg-elevated text-muted">
              <tr>
                <th class="px-3 py-2">When</th>
                <th class="px-3 py-2">Type</th>
                <th class="px-3 py-2">Merchant</th>
                <th class="px-3 py-2">State</th>
                <th class="px-3 py-2 text-right">Amount</th>
                <th class="px-3 py-2 text-right">Discount</th>
              </tr>
            </thead>
            <tbody>
              {#each view.recent_transactions_full ?? [] as t}
                <tr class="border-t border-border">
                  <td class="px-3 py-1.5 text-muted">{fmtRelative(t.timestamp)}</td>
                  <td class="px-3 py-1.5">{t.transaction_type}</td>
                  <td class="px-3 py-1.5 font-mono text-fg/80">{t.merchant_id}</td>
                  <td class="px-3 py-1.5">{t.location?.state ?? '—'}</td>
                  <td class="px-3 py-1.5 text-right">{fmtMyr(t.amount)}</td>
                  <td class="px-3 py-1.5 text-right text-warn">{t.discount_amount ? fmtMyr(t.discount_amount) : '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </Section>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Section title="Open cases" subtitle={`${view.open_cases?.length ?? 0}`}>
        {#if (view.open_cases?.length ?? 0) === 0}
          <p class="text-sm text-muted">No open cases.</p>
        {:else}
          <ul class="divide-y divide-border">
            {#each view.open_cases ?? [] as c}
              <li class="flex items-center gap-3 py-2 text-sm">
                <SeverityBadge severity={c.severity} />
                <a class="font-mono text-accent hover:underline" href={`/quarantine/${c.case_id}`}>{c.case_id}</a>
                <span class="text-muted">{fmtRelative(c.created_at)}</span>
                <span class="ml-auto">{fmtMyr(c.amount)}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </Section>

      <Section title="Online features" subtitle="Sub-minute rolling">
        {#if !view.features}
          <p class="text-sm text-muted">No features yet (run the feature engineer worker).</p>
        {:else}
          <JsonView value={view.features} />
        {/if}
      </Section>
    </div>

    <NextBestOfferCard customerId={view.customer_id} initial={view.recommendations ?? null} />

    <!-- H1: Customer refresh & analytics (PR-13) -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <CustomerRefreshPanel customerId={view.customer_id} />
      <TransactionPatternPanel customerId={view.customer_id} />
    </div>
  {:else if loading}
    <div class="text-sm text-muted">Loading…</div>
  {/if}
</div>
