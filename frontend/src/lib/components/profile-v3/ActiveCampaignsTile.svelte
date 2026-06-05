<script lang="ts">
  import type { ActiveCampaign } from "$lib/types";
  import { fmtMyr, fmtRelative } from "$lib/utils";
  import { Sparkles, Hand, FlaskConical, ChevronDown, ChevronRight } from "lucide-svelte";

  interface Props { campaigns: ActiveCampaign[] }
  let { campaigns }: Props = $props();

  let openId = $state<string | null>(null);

  function enrolledChip(by: string) {
    if (by === "ml_signal") return { Icon: Sparkles, cls: "pill bg-accent/15 text-accent text-[10px]" };
    if (by === "rule") return { Icon: FlaskConical, cls: "pill bg-accent2/15 text-accent2 text-[10px]" };
    return { Icon: Hand, cls: "pill bg-muted/15 text-muted text-[10px]" };
  }

  function statusClass(s: string): string {
    if (s === "converted") return "pill pill-ok text-[10px]";
    if (s === "in_flight") return "pill pill-warn text-[10px]";
    if (s === "expired") return "pill pill-muted text-[10px]";
    return "pill pill-accent text-[10px]";
  }
</script>

<section class="card p-5">
  <header class="mb-3">
    <h2 class="text-lg font-semibold">Active campaigns</h2>
    <p class="text-xs text-muted">{campaigns.length} enrollments with ML reasoning</p>
  </header>

  {#if campaigns.length === 0}
    <p class="text-sm text-muted">No active campaigns.</p>
  {:else}
    <ul class="space-y-2">
      {#each campaigns as c}
        {@const open = openId === c.enrollment_id}
        {@const { Icon, cls } = enrolledChip(c.enrolled_by)}
        <li class="rounded-lg border border-border bg-elevated/40">
          <button
            class="w-full p-3 text-left"
            onclick={() => (openId = open ? null : c.enrollment_id)}
          >
            <div class="flex items-center gap-2">
              {#if open}<ChevronDown size="14" class="text-muted" />{:else}<ChevronRight size="14" class="text-muted" />{/if}
              <span class={cls}><Icon size="11" /> {c.enrolled_by}</span>
              <span class="text-sm font-medium">{c.campaign_name}</span>
              <span class="ml-auto flex items-center gap-2 text-xs">
                <span class="text-muted">{c.recommended_channel}</span>
                <span class={statusClass(c.status)}>{c.status}</span>
              </span>
            </div>
            <div class="mt-1 ml-5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted">
              <span>Uplift <span class="text-ok">{fmtMyr(c.expected_ltv_uplift)}</span></span>
              <span>Sim. conv <span class="text-fg/80">{(c.similar_customer_conversion_rate * 100).toFixed(0)}%</span></span>
              <span>Enrolled {fmtRelative(c.enrolled_date)}</span>
              {#if c.revenue_realized_myr != null}
                <span>Realized <span class="text-ok">{fmtMyr(c.revenue_realized_myr)}</span></span>
              {/if}
            </div>
          </button>
          {#if open}
            <div class="border-t border-border px-3 py-2.5 text-xs">
              <p class="text-fg/80">{c.reasoning}</p>
              {#if c.similar_customers_sampled?.length}
                <div class="mt-2">
                  <div class="text-muted mb-1">Similar customers sampled</div>
                  <div class="flex flex-wrap gap-1">
                    {#each c.similar_customers_sampled as cid}
                      <a class="font-mono text-[11px] text-accent hover:underline" href={`/customers/${cid}`}>{cid}</a>
                    {/each}
                  </div>
                </div>
              {/if}
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>
