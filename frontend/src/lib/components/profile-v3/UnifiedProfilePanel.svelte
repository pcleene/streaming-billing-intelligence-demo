<script lang="ts">
  import type { UnifiedProfile, ChannelOptIn } from "$lib/types";
  import { Mail, Phone, BellOff, MapPin, Languages, Clock } from "lucide-svelte";
  import MalaysiaStateDot from "$lib/components/profile-v3/MalaysiaStateDot.svelte";

  interface Props {
    profile: UnifiedProfile | null | undefined;
    customerId: string;
  }
  let { profile, customerId }: Props = $props();

  const channelTint: Record<string, string> = {
    email: "bg-accent/15 text-accent",
    sms: "bg-ok/15 text-ok",
    push_notification: "bg-accent2/15 text-accent2",
    whatsapp: "bg-ok/15 text-ok",
    acme_app_inbox: "bg-warn/15 text-warn"
  };

  const optIns = $derived(profile?.contact?.channel_opt_ins ?? []);
  const dnd = $derived(profile?.contact?.communication_preferences?.do_not_disturb === true);

  function chipClass(c: ChannelOptIn): string {
    const base = channelTint[c.channel] ?? "bg-muted/15 text-muted";
    return c.opted_in
      ? `pill ${base} border border-transparent`
      : `pill bg-muted/10 text-muted line-through border border-dashed border-border`;
  }
</script>

{#if !profile}
  <section class="card p-5">
    <header class="mb-2">
      <h2 class="text-lg font-semibold">Profile</h2>
      <p class="text-xs text-muted">
        <span class="font-mono">{customerId}</span> · Unified profile not available
      </p>
    </header>
    <p class="text-sm text-muted">
      The backend did not return a V3 <code class="font-mono">unified_profile</code> for this customer.
    </p>
  </section>
{:else}
<section class="card p-5">
  <header class="mb-4 flex items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-2">
        <h2 class="text-lg font-semibold">{profile.name ?? customerId}</h2>
        {#if profile.ethnicity}
          <span class="pill pill-muted text-[10px]">{profile.ethnicity}</span>
        {/if}
        {#if dnd}
          <span class="pill pill-warn text-[10px]" title="Do not disturb is on">
            <BellOff size="11" /> DND
          </span>
        {/if}
      </div>
      <div class="mt-1 text-xs text-muted">
        <span class="font-mono">{customerId}</span>
        {#if profile.ic_number} · IC <span class="font-mono">{profile.ic_number}</span>{/if}
        {#if profile.date_of_birth} · DOB {profile.date_of_birth}{/if}
      </div>
    </div>
    {#if profile.address?.state}
      <div class="shrink-0">
        <MalaysiaStateDot state={profile.address.state} />
      </div>
    {/if}
  </header>

  <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
    <div>
      <div class="text-xs uppercase tracking-wide text-muted mb-2">Contact</div>
      <dl class="space-y-1.5 text-sm">
        <div class="flex items-center gap-2">
          <Mail size="14" class="text-muted" />
          <span class="font-mono text-fg/90 truncate">{profile.contact?.email ?? "—"}</span>
        </div>
        <div class="flex items-center gap-2">
          <Phone size="14" class="text-muted" />
          <span class="font-mono">{profile.contact?.phone ?? "—"}</span>
        </div>
      </dl>

      <div class="mt-3 text-xs uppercase tracking-wide text-muted mb-2">Channel opt-ins</div>
      <div class="flex flex-wrap gap-1.5">
        {#each optIns as c}
          <span class={chipClass(c)} title={c.opted_in ? `Opted in ${c.opted_in_date}` : "Opted out"}>
            {c.channel}
          </span>
        {/each}
        {#if optIns.length === 0}
          <span class="text-xs text-muted">No opt-ins recorded</span>
        {/if}
      </div>
    </div>

    <div>
      <div class="text-xs uppercase tracking-wide text-muted mb-2">Address</div>
      <div class="flex items-start gap-2 text-sm">
        <MapPin size="14" class="mt-0.5 text-muted" />
        <div>
          <div>{profile.address?.street ?? "—"}</div>
          <div class="text-muted">
            {profile.address?.postcode ?? ""}
            {profile.address?.city ?? ""}{profile.address?.city ? "," : ""}
            {profile.address?.state ?? ""}
          </div>
        </div>
      </div>

      <div class="mt-3 text-xs uppercase tracking-wide text-muted mb-2">Preferences</div>
      <dl class="space-y-1.5 text-sm">
        <div class="flex items-center gap-2">
          <Languages size="14" class="text-muted" />
          <span>{profile.contact?.communication_preferences?.preferred_language ?? "—"}</span>
        </div>
        <div class="flex items-center gap-2">
          <Clock size="14" class="text-muted" />
          <span class="text-muted">Quiet</span>
          <span class="font-mono">
            {profile.contact?.communication_preferences?.quiet_hours_start ?? "?"}
            –
            {profile.contact?.communication_preferences?.quiet_hours_end ?? "?"}
          </span>
        </div>
      </dl>
    </div>
  </div>
</section>
{/if}
