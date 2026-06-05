<script lang="ts">
  import type { EntityKey, EntityProfiles } from "$lib/types";
  import EntityCardPayTv from "./EntityCardPayTv.svelte";
  import EntityCardStreaming from "./EntityCardStreaming.svelte";
  import EntityCardBroadband from "./EntityCardBroadband.svelte";
  import EntityCardPrepaid from "./EntityCardPrepaid.svelte";
  import EntityCardBusiness from "./EntityCardBusiness.svelte";
  import EntityCardCards from "./EntityCardCards.svelte";

  interface Props {
    entities: EntityKey[];
    profiles: EntityProfiles;
  }
  let { entities, profiles }: Props = $props();
</script>

<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
  {#each entities as e (e)}
    {#if e === "acme_paytv" && profiles.acme_paytv}
      <EntityCardPayTv profile={profiles.acme_paytv} />
    {:else if e === "acme_streaming" && profiles.acme_streaming}
      <EntityCardStreaming profile={profiles.acme_streaming} />
    {:else if e === "acme_broadband" && profiles.acme_broadband}
      <EntityCardBroadband profile={profiles.acme_broadband} />
    {:else if e === "acme_prepaid" && profiles.acme_prepaid}
      <EntityCardPrepaid profile={profiles.acme_prepaid} />
    {:else if e === "acme_business" && profiles.acme_business}
      <EntityCardBusiness profile={profiles.acme_business} />
    {:else if e === "acme_cards" && profiles.acme_cards}
      <EntityCardCards profile={profiles.acme_cards} />
    {:else}
      <div class="card p-4 text-xs text-muted">
        <div class="font-medium text-fg/80">{e}</div>
        <div class="mt-1">No entity profile recorded.</div>
      </div>
    {/if}
  {/each}
  {#if entities.length === 0}
    <div class="card p-4 text-sm text-muted">No entities recorded.</div>
  {/if}
</div>
