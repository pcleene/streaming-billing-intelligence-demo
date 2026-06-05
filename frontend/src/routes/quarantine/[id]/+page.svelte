<script lang="ts">
  import { page } from "$app/state";
  import { quarantineApi } from "$lib/api";
  import type { AssistResponse, QuarantineCase } from "$lib/types";
  import type { RelatedCustomerCases } from "$lib/api";
  import Section from "$lib/components/Section.svelte";
  import SeverityBadge from "$lib/components/SeverityBadge.svelte";
  import AssistPanel from "$lib/components/AssistPanel.svelte";
  import LangGraphTraceViewer from "$lib/components/LangGraphTraceViewer.svelte";
  import BeforeAfterPanel from "$lib/components/BeforeAfterPanel.svelte";
  import IForestScoreCard from "$lib/components/IForestScoreCard.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";
  import { fmtDate, fmtMyr, fmtRelative } from "$lib/utils";
  import { formatRuleEvidence } from "$lib/components/rule_evidence";

  // Effective transaction amount falls back through several locations
  // because synthetic cases populate them inconsistently:
  //   case.amount → transaction_summary.total_myr → revenue_impact.amount_at_risk_myr
  function effectiveAmount(c: any): number | null {
    if (typeof c?.amount === "number") return c.amount;
    const ts = c?.transaction_summary;
    if (ts && typeof ts.total_myr === "number") return ts.total_myr;
    if (ts && typeof ts.amount_myr === "number") return ts.amount_myr;
    if (ts && typeof ts.amount === "number") return ts.amount;
    const ri = c?.revenue_impact;
    if (ri && typeof ri.amount_at_risk_myr === "number" && ri.amount_at_risk_myr > 0) {
      return ri.amount_at_risk_myr;
    }
    return null;
  }

  // Filter rationale lines that the deterministic synthesizer emits with
  // empty/zero signal — they confuse analysts more than help. Keeps the
  // backend wire shape intact while rendering only useful bullets.
  function filterRationale(items: unknown): string[] {
    if (!Array.isArray(items)) return [];
    const noisy = [
      /^Customer 30d pattern: 0 txns/i,
      /Rule fired 0 times/i,
      /across 0 customers/i,
      /avg 0\.00\b/i
    ];
    return items
      .map((s) => (typeof s === "string" ? s : ""))
      .filter((s) => s && !noisy.some((re) => re.test(s)));
  }

  const id = $derived(page.params.id ?? "");
  let kase = $state<QuarantineCase | null>(null);
  let error = $state<string | null>(null);

  let assist = $state<AssistResponse | null>(null);
  let assistLoading = $state(false);

  let related = $state<RelatedCustomerCases | null>(null);

  let analystId = $state("analyst_demo");
  let analystNotes = $state("");
  let dispositioning = $state(false);

  // Mongo-stream-vs-warehouse-batch lives in a modal so the main scroll
  // stays focused on case info; analysts pop it open on demand.
  let showBeforeAfter = $state(false);

  // The persisted ai_assist projection (with agent findings + trace)
  // shipped on the case doc itself. The backend's `AiAssist` Pydantic
  // model is allowed to carry extra fields via `[key: string]: unknown`
  // on the typing side; keep this read permissive.
  const aiAssist = $derived<Record<string, any> | null>(
    (kase as any)?.ai_assist ?? null
  );
  // Show the structured findings panel whenever the case has a persisted
  // summary, regardless of whether the agent variant or the linear RAG
  // path produced it. Linear runs never emit a trace, so gating on
  // `agent_trace.length > 0` would silently hide those analyses.
  const hasAiAssist = $derived(
    !!aiAssist && typeof aiAssist.summary === "string" && aiAssist.summary.length > 0
  );
  const hasAgentTrace = $derived(
    !!aiAssist && Array.isArray(aiAssist.agent_trace) && aiAssist.agent_trace.length > 0
  );
  // Denormalised similars from the persisted `similar_cases_preview`
  // (written by `AiAssistService` at the case root, alongside
  // `ai_assist`). Falls back to the ephemeral `/ai-assist` response
  // similar_cases when the analyst just ran Re-run. We also bridge the
  // `relevance` key (preview shape) to `score` so the row template
  // only has one branch.
  const similarsPreview = $derived<Array<Record<string, any>>>(
    Array.isArray((kase as any)?.similar_cases_preview)
      ? ((kase as any).similar_cases_preview as Array<Record<string, any>>).map((p) => ({
          ...p,
          score: typeof p.score === "number" ? p.score : p.relevance
        }))
      : []
  );
  const txn = $derived<Record<string, any>>(
    ((kase as any)?.transaction_summary ?? {}) as Record<string, any>
  );

  async function loadCase() {
    try {
      kase = await quarantineApi.get(id, { inspect: inspector.open });
      error = null;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "load failed";
      kase = null;
    }
  }

  async function loadRelated() {
    try {
      related = await quarantineApi.relatedCustomer(id);
    } catch {
      related = null;
    }
  }

  // Materialise the persisted `ai_assist` projection on the case (full
  // agentic path when `FF_AI_ASSIST_AGENTIC=true`, otherwise the linear
  // RAG path). Forces a fresh run so the analyst sees current state, and
  // refreshes the case doc so the structured findings panel + trace
  // appear without a separate fetch. Returns the ephemeral
  // `AssistResponse` so we can also render `similar_cases` inline.
  async function runAssist() {
    assistLoading = true;
    try {
      assist = await quarantineApi.aiAssist(id, true, { inspect: inspector.open });
      await loadCase();
    } catch (e: unknown) {
      assist = null;
      error = e instanceof Error ? e.message : "assist failed";
    } finally {
      assistLoading = false;
    }
  }

  async function disposition(d: string) {
    dispositioning = true;
    try {
      kase = await quarantineApi.disposition(id, {
        disposition: d,
        analyst_id: analystId,
        analyst_notes: analystNotes
      });
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : "disposition failed";
    } finally {
      dispositioning = false;
    }
  }

  $effect(() => {
    loadCase();
    loadRelated();
  });
  $effect(() => {
    if (inspector.open) loadCase();
  });
</script>

<div class="space-y-6">
  <header class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <h1 class="text-2xl font-semibold flex items-center gap-3">
        Case <span class="font-mono text-accent">{id}</span>
        {#if kase}<SeverityBadge severity={kase.severity} />{/if}
        {#if kase}<span class="pill pill-muted text-xs uppercase">{kase.status}</span>{/if}
      </h1>
      {#if kase}
        <p class="text-sm text-muted">
          Customer
          <a class="font-mono text-fg/80 hover:underline" href={`/customers/${kase.customer_id}`}>
            {kase.customer_id}
          </a>
          · opened {fmtRelative(kase.created_at)}
        </p>
      {/if}
    </div>
    <div class="flex flex-wrap gap-2">
      <a class="btn" href="/quarantine">Back</a>
      <button
        class="btn"
        type="button"
        disabled={!kase}
        onclick={() => (showBeforeAfter = true)}
        title="Compare what a 24h-stale warehouse would have shown vs the live operational doc"
      >
        Stream vs batch
      </button>
      <button class="btn btn-primary" onclick={runAssist} disabled={assistLoading || !kase}>
        {assistLoading ? "Asking Claude…" : "Ask AI Assist"}
      </button>
    </div>
  </header>

  {#if kase}
    {@const headlineAmount = effectiveAmount(kase)}
    {@const headlineTxn = (kase as any).transaction_id ?? txn.transaction_id}
    {@const headlineRule = kase.rules_triggered?.[0]}
    <!-- At-a-glance headline strip — gives the analyst the four numbers
         they always look for without scrolling. -->
    <div class="card flex flex-wrap items-stretch gap-x-6 gap-y-3 px-4 py-3">
      <div class="flex flex-col">
        <span class="text-[10px] uppercase tracking-wider text-muted">Amount at risk</span>
        <span class="text-xl font-semibold tabular-nums">{fmtMyr(headlineAmount)}</span>
      </div>
      <div class="flex flex-col">
        <span class="text-[10px] uppercase tracking-wider text-muted">Transaction</span>
        <span class="font-mono text-sm">{headlineTxn ?? "—"}</span>
      </div>
      {#if headlineRule}
        <div class="flex flex-col">
          <span class="text-[10px] uppercase tracking-wider text-muted">Headline rule</span>
          <span class="text-sm font-medium">
            {headlineRule.rule_name}
            <span class="ml-1 pill pill-accent font-mono text-[10px]">{headlineRule.rule_type}</span>
          </span>
        </div>
      {/if}
      {#if (kase as any).priority_band}
        <div class="flex flex-col">
          <span class="text-[10px] uppercase tracking-wider text-muted">Priority</span>
          <span class="text-sm font-medium">
            {(kase as any).priority_band}
            {#if typeof (kase as any).priority_score === "number"}
              <span class="text-muted">· {(kase as any).priority_score.toFixed(1)}</span>
            {/if}
          </span>
        </div>
      {/if}
      {#if (kase as any).sla}
        {@const sla = (kase as any).sla}
        <div class="flex flex-col">
          <span class="text-[10px] uppercase tracking-wider text-muted">SLA</span>
          <span class="text-sm font-medium {sla.is_breached ? 'text-danger' : ''}">
            {sla.is_breached
              ? "breached"
              : typeof sla.minutes_to_breach === "number"
                ? `${Math.max(0, Math.round(sla.minutes_to_breach))} min left`
                : "—"}
          </span>
        </div>
      {/if}
      {#if hasAiAssist}
        <div class="ml-auto flex items-center">
          <span class="pill pill-accent">
            {hasAgentTrace ? "Agent reviewed" : "AI reviewed"}
          </span>
        </div>
      {/if}
    </div>
  {/if}

  {#if error}<div class="card p-3 text-sm text-danger">{error}</div>{/if}

  {#if kase}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div class="lg:col-span-2 space-y-4">
        <!-- Case summary: rules + transaction context in two columns. -->
        <Section title="Case summary">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <div class="mb-2 text-xs uppercase tracking-wide text-muted">Rules triggered</div>
              <ul class="space-y-2">
                {#each kase.rules_triggered as r}
                  {@const fe = formatRuleEvidence(r.rule_type, r.evidence)}
                  <li class="rounded-lg border border-border bg-elevated p-3">
                    <div class="flex items-baseline gap-2">
                      <span class="text-sm font-medium">{r.rule_name}</span>
                      <span class="pill pill-accent font-mono text-[10px]">{r.rule_type}</span>
                      {#if r.score != null}
                        <span class="ml-auto text-xs text-muted">score {r.score.toFixed(3)}</span>
                      {/if}
                    </div>
                    {#if fe.summary}
                      <div class="mt-1 text-sm text-fg/80">{fe.summary}</div>
                    {/if}
                    {#if fe.chips.length > 0}
                      <div class="mt-2 flex flex-wrap gap-1">
                        {#each fe.chips as c}
                          <span class="pill pill-muted text-[10px]">
                            <span class="text-muted">{c.key}:</span>&nbsp;{c.value}
                          </span>
                        {/each}
                      </div>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
            <div class="space-y-4">
              <div>
                <div class="mb-2 text-xs uppercase tracking-wide text-muted">Transaction</div>
                <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  {#if txn.transaction_type}
                    <dt class="text-muted">Type</dt>
                    <dd class="font-mono text-xs">{txn.transaction_type}</dd>
                  {/if}
                  {#if txn.channel}
                    <dt class="text-muted">Channel</dt>
                    <dd>{txn.channel}</dd>
                  {/if}
                  <dt class="text-muted">Opened</dt>
                  <dd>{fmtDate(kase.created_at)}</dd>
                  {#if txn.timestamp && txn.timestamp !== kase.created_at}
                    <dt class="text-muted">Txn at</dt>
                    <dd>{fmtDate(txn.timestamp)}</dd>
                  {/if}
                </dl>
                {#if txn.payment_method || (typeof txn.items_count === "number" && txn.items_count > 0) || (typeof txn.total_discount_myr === "number" && txn.total_discount_myr > 0)}
                  <div class="mt-2 flex flex-wrap gap-1">
                    {#if txn.payment_method}
                      <span class="pill pill-muted">{txn.payment_method}{txn.card_last4 ? ` ••${txn.card_last4}` : ""}</span>
                    {/if}
                    {#if typeof txn.items_count === "number" && txn.items_count > 0}
                      <span class="pill pill-muted">{txn.items_count} item{txn.items_count === 1 ? "" : "s"}</span>
                    {/if}
                    {#if typeof txn.total_discount_myr === "number" && txn.total_discount_myr > 0}
                      <span class="pill pill-muted">discount {fmtMyr(txn.total_discount_myr)}</span>
                    {/if}
                  </div>
                {/if}
              </div>

              {#if kase.customer_snapshot}
                {@const snap = kase.customer_snapshot as Record<string, any>}
                <div>
                  <div class="mb-2 text-xs uppercase tracking-wide text-muted">Customer</div>
                  <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                    {#if snap.name}
                      <dt class="text-muted">Name</dt>
                      <dd>{snap.name}</dd>
                    {/if}
                    {#if snap.tier}
                      <dt class="text-muted">Tier</dt>
                      <dd>{snap.tier}</dd>
                    {/if}
                    {#if snap.package_at_billing}
                      <dt class="text-muted">Package</dt>
                      <dd class="font-mono text-xs">{snap.package_at_billing}</dd>
                    {/if}
                    {#if snap.service_state ?? snap.state}
                      <dt class="text-muted">State</dt>
                      <dd>{snap.service_state ?? snap.state}</dd>
                    {/if}
                    {#if snap.segment}
                      <dt class="text-muted">Segment</dt>
                      <dd>{snap.segment}</dd>
                    {/if}
                  </dl>
                  <div class="mt-2 flex flex-wrap gap-1">
                    {#if typeof snap.tenure_months === "number" && snap.tenure_months > 0}
                      <span class="pill pill-muted">tenure {snap.tenure_months}mo</span>
                    {/if}
                    {#if typeof snap.lifetime_quarantine_count === "number"}
                      <span class="pill {snap.lifetime_quarantine_count > 2 ? 'pill-warn' : 'pill-muted'}">
                        {snap.lifetime_quarantine_count} prior case{snap.lifetime_quarantine_count === 1 ? "" : "s"}
                      </span>
                    {/if}
                    {#if typeof snap.churn_risk === "number" && snap.churn_risk > 0}
                      <span class="pill {snap.churn_risk >= 0.5 ? 'pill-warn' : 'pill-muted'}">
                        churn risk {(snap.churn_risk * 100).toFixed(0)}%
                      </span>
                    {/if}
                    {#if Array.isArray(snap.active_entitlements_at_billing) && snap.active_entitlements_at_billing.length > 0}
                      <span class="pill pill-muted">
                        {snap.active_entitlements_at_billing.length} entitlement{snap.active_entitlements_at_billing.length === 1 ? "" : "s"}
                      </span>
                    {/if}
                  </div>
                </div>
              {/if}
            </div>
          </div>
        </Section>


        <!-- Structured AI findings — shown whenever the case has a
             persisted summary (linear or agentic). The trace card below
             gates on `hasAgentTrace` separately. -->
        {#if hasAiAssist && aiAssist}
          {@const cleanRationale = filterRationale(aiAssist.rationale)}
          <Section
            title={hasAgentTrace ? "Agent findings" : "AI assist findings"}
            subtitle={hasAgentTrace
              ? "LangGraph AI-assist projection"
              : "Linear RAG projection (agentic flow disabled)"}
          >
            {#if aiAssist.degraded}
              <div class="mb-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                <span class="font-semibold">Degraded run.</span>
                Some upstream tools failed so the agent fell back to deterministic logic.
                {#if aiAssist.degraded_reason}
                  <div class="mt-1 font-mono text-[11px] text-warn/90">{aiAssist.degraded_reason}</div>
                {/if}
              </div>
            {/if}
            <p class="text-sm leading-relaxed">{aiAssist.summary}</p>
            <div class="mt-4 flex flex-wrap gap-2">
              <span class="pill pill-accent">likelihood: {aiAssist.likelihood}</span>
              {#if typeof aiAssist.confidence === "number"}
                <span class="pill pill-muted">confidence: {(aiAssist.confidence * 100).toFixed(0)}%</span>
              {/if}
            </div>

            {#if cleanRationale.length > 0}
              <div class="mt-4">
                <div class="text-xs uppercase text-muted">Rationale</div>
                <ul class="mt-2 list-inside list-disc space-y-1 text-sm">
                  {#each cleanRationale as r}<li>{r}</li>{/each}
                </ul>
              </div>
            {:else if Array.isArray(aiAssist.rationale) && aiAssist.rationale.length > 0}
              <p class="mt-4 text-sm text-muted italic">
                No load-bearing signal — analytics tools returned empty results. Treat
                this run as a deterministic fallback only.
              </p>
            {/if}

            {#if Array.isArray(aiAssist.recommended_steps) && aiAssist.recommended_steps.length > 0}
              <div class="mt-4">
                <div class="text-xs uppercase text-muted">Recommended steps</div>
                <ol class="mt-2 list-inside list-decimal space-y-1 text-sm">
                  {#each aiAssist.recommended_steps as s}<li>{s}</li>{/each}
                </ol>
              </div>
            {/if}

            {#if Array.isArray(aiAssist.references) && aiAssist.references.length > 0}
              <div class="mt-4">
                <div class="text-xs uppercase text-muted">Citations</div>
                <ul class="mt-2 space-y-1 text-xs font-mono">
                  {#each aiAssist.references as r}
                    <li class="flex items-center gap-2">
                      {#if r.case_id}
                        <a class="text-accent hover:underline" href={`/quarantine/${r.case_id}`}>{r.case_id}</a>
                      {/if}
                      {#if r.disposition}<span class="pill pill-muted">{r.disposition}</span>{/if}
                      {#if r.score != null}<span class="text-muted">score {Number(r.score).toFixed(3)}</span>{/if}
                      {#if r.why_relevant}<span class="text-muted">— {r.why_relevant}</span>{/if}
                    </li>
                  {/each}
                </ul>
              </div>
            {/if}

            <div class="mt-4 text-[11px] text-muted">
              Reviewed by agent
              {#if aiAssist.generated_at}at {fmtRelative(aiAssist.generated_at)}{/if}
              {#if aiAssist.model}, model: <span class="font-mono">{aiAssist.model}</span>{/if}
            </div>
          </Section>
        {/if}

        <!-- Same-customer related cases — analysts reach for this more
             often than the trace, so it sits above the trace card. -->
        {#if related && (related.open.length > 0 || related.history.length > 0)}
          <Section title="Same customer — related cases">
            {#if related.open.length > 0}
              <div class="mb-3 text-xs uppercase tracking-wide text-muted">Other open cases</div>
              <div class="overflow-hidden rounded-lg border border-border">
                <table class="w-full text-left text-sm">
                  <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th class="px-3 py-2">Case</th>
                      <th class="px-3 py-2">Rule</th>
                      <th class="px-3 py-2 text-right">Amount</th>
                      <th class="px-3 py-2">Severity</th>
                      <th class="px-3 py-2">Status</th>
                      <th class="px-3 py-2 text-right">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each related.open as r}
                      <tr class="border-t border-border hover:bg-elevated/60">
                        <td class="px-3 py-2">
                          <a class="font-mono text-accent hover:underline" href={`/quarantine/${r.case_id}`}>{r.case_id}</a>
                          {#if r.transaction_id}
                            <div class="font-mono text-[10px] text-muted">{r.transaction_id}</div>
                          {/if}
                        </td>
                        <td class="px-3 py-2 text-xs">
                          <div>{r.rule_name ?? "—"}</div>
                          {#if r.rule_type}
                            <span class="pill pill-accent font-mono text-[10px]">{r.rule_type}</span>
                          {/if}
                        </td>
                        <td class="px-3 py-2 text-right tabular-nums">{fmtMyr(r.amount)}</td>
                        <td class="px-3 py-2"><SeverityBadge severity={r.severity ?? "low"} /></td>
                        <td class="px-3 py-2"><span class="pill pill-muted">{r.status ?? "—"}</span></td>
                        <td class="px-3 py-2 text-right text-muted">{fmtRelative(r.created_at)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}

            {#if related.history.length > 0}
              <div class="mt-4 mb-3 text-xs uppercase tracking-wide text-muted">Resolved history</div>
              <div class="overflow-hidden rounded-lg border border-border">
                <table class="w-full text-left text-sm">
                  <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th class="px-3 py-2">Case</th>
                      <th class="px-3 py-2">Rule</th>
                      <th class="px-3 py-2 text-right">Amount</th>
                      <th class="px-3 py-2">Severity</th>
                      <th class="px-3 py-2">Disposition</th>
                      <th class="px-3 py-2 text-right">Resolved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each related.history as r}
                      <tr class="border-t border-border hover:bg-elevated/60">
                        <td class="px-3 py-2">
                          <a class="font-mono text-accent hover:underline" href={`/quarantine/${r.case_id}`}>{r.case_id}</a>
                          {#if r.transaction_id}
                            <div class="font-mono text-[10px] text-muted">{r.transaction_id}</div>
                          {/if}
                        </td>
                        <td class="px-3 py-2 text-xs">
                          <div>{r.rule_name ?? "—"}</div>
                          {#if r.rule_type}
                            <span class="pill pill-accent font-mono text-[10px]">{r.rule_type}</span>
                          {/if}
                        </td>
                        <td class="px-3 py-2 text-right tabular-nums">{fmtMyr(r.amount)}</td>
                        <td class="px-3 py-2"><SeverityBadge severity={r.severity ?? "low"} /></td>
                        <td class="px-3 py-2"><span class="pill pill-muted">{r.disposition ?? "—"}</span></td>
                        <td class="px-3 py-2 text-right text-muted">{fmtRelative(r.resolved_at)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          </Section>
        {/if}

        <!-- LangGraph trace lives below findings + related cases as
             operator/diagnostic info. Only show when an agent trace
             actually exists for this case — otherwise the card just
             renders an empty-state with a Re-run button, which is
             noisy when AI assist hasn't run yet. -->
        {#if hasAgentTrace}
          <LangGraphTraceViewer caseId={id} />
        {/if}

        <!-- Retrieved similar resolved cases: prefer the persisted
             denormalised preview, fall back to the fresh `/ai-assist`
             response when the user just hit Re-run. -->
        {#if similarsPreview.length > 0 || assist?.similar_cases?.length}
          {@const list = similarsPreview.length > 0
            ? similarsPreview
            : (assist?.similar_cases ?? [])}
          <Section
            title="Retrieved similar resolved cases"
            subtitle="Top vector matches over the history corpus"
          >
            <ul class="space-y-2 text-sm">
              {#each list as s}
                <li class="rounded-lg border border-border p-3">
                  <div class="flex flex-wrap items-center gap-2">
                    {#if s.case_id}
                      <a class="font-mono text-accent hover:underline" href={`/quarantine/${s.case_id}`}>
                        {s.case_id}
                      </a>
                    {/if}
                    {#if s.disposition}<span class="pill pill-muted">{s.disposition}</span>{/if}
                    {#if s.rule_type}
                      <span class="pill pill-accent font-mono text-[10px]">{s.rule_type}</span>
                    {/if}
                    {#if typeof s.score === "number"}
                      <span class="ml-auto text-xs text-muted">{s.score.toFixed(3)}</span>
                    {/if}
                  </div>
                  {#if s.why_relevant}
                    <p class="mt-1 text-xs text-fg/80">{s.why_relevant}</p>
                  {:else if s.analyst_notes}
                    <p class="mt-1 text-xs text-muted">{s.analyst_notes}</p>
                  {/if}
                </li>
              {/each}
            </ul>
          </Section>
        {/if}
      </div>

      <div class="space-y-4">
        <!-- When the case already has a persisted AI assist projection
             we render the rich "Agent findings" / "AI assist findings"
             card on the left. Show the lightweight AssistPanel only
             when nothing has been written yet AND the analyst is in
             the middle of running it for the first time. -->
        {#if !hasAiAssist}
          <AssistPanel data={assist} loading={assistLoading} />
        {/if}

        <!-- PR-FE-3: anomaly score panel for the customer behind this case -->
        {#if kase}<IForestScoreCard customerId={kase.customer_id} />{/if}

        <Section title="Disposition" subtitle="Analyst override is recorded for the model loop">
          <div class="space-y-3">
            <label class="block text-sm">
              <span class="mb-1 block text-muted">Analyst ID</span>
              <input class="input" bind:value={analystId} />
            </label>
            <label class="block text-sm">
              <span class="mb-1 block text-muted">Notes</span>
              <textarea class="input min-h-20" bind:value={analystNotes}></textarea>
            </label>
            {#if kase.status === "resolved"}
              <p class="text-sm text-muted">
                Resolved {fmtRelative(kase.resolved_at)} as <strong>{kase.disposition}</strong>.
              </p>
            {:else}
              <div class="grid grid-cols-2 gap-2">
                <button class="btn" disabled={dispositioning} onclick={() => disposition('false_positive')}>False positive</button>
                <button class="btn btn-primary" disabled={dispositioning} onclick={() => disposition('true_positive')}>True positive</button>
                <button class="btn col-span-2" disabled={dispositioning} onclick={() => disposition('escalate')}>Escalate</button>
              </div>
            {/if}
          </div>
        </Section>
      </div>
    </div>
  {/if}

  {#if showBeforeAfter}
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Stream vs warehouse batch comparison"
      onclick={(e) => { if (e.target === e.currentTarget) showBeforeAfter = false; }}
      onkeydown={(e) => { if (e.key === "Escape") showBeforeAfter = false; }}
      tabindex="-1"
    >
      <div class="w-full max-w-4xl rounded-lg border border-border bg-bg shadow-xl">
        <div class="flex items-center justify-between border-b border-border px-4 py-2">
          <h2 class="text-sm font-semibold">Mongo stream vs warehouse batch</h2>
          <button class="btn" type="button" onclick={() => (showBeforeAfter = false)}>Close</button>
        </div>
        <div class="max-h-[80vh] overflow-y-auto p-4">
          <BeforeAfterPanel caseId={id} />
        </div>
      </div>
    </div>
  {/if}
</div>
