<script lang="ts">
  import type { Equipment } from "$lib/types";
  import { fmtRelative } from "$lib/utils";

  interface Props { equipment: Equipment[] }
  let { equipment }: Props = $props();

  function rowClass(status: string): string {
    if (status === "swap_requested") return "bg-warn/5";
    if (status === "returned") return "opacity-60";
    return "";
  }
  function statusPill(status: string): string {
    if (status === "active") return "pill pill-ok text-[10px]";
    if (status === "swap_requested") return "pill pill-warn text-[10px]";
    return "pill pill-muted text-[10px]";
  }
</script>

<section class="card p-5">
  <header class="mb-3">
    <h2 class="text-lg font-semibold">Equipment</h2>
    <p class="text-xs text-muted">{equipment.length} units on file</p>
  </header>

  {#if equipment.length === 0}
    <p class="text-sm text-muted">No equipment recorded.</p>
  {:else}
    <div class="overflow-hidden rounded-lg border border-border">
      <table class="w-full text-left text-xs">
        <thead class="bg-elevated text-muted">
          <tr>
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2">Model</th>
            <th class="px-3 py-2">Serial</th>
            <th class="px-3 py-2">Installed</th>
            <th class="px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {#each equipment as e}
            <tr class="border-t border-border {rowClass(e.status)}">
              <td class="px-3 py-1.5 capitalize">{e.type.replace(/_/g, " ")}</td>
              <td class="px-3 py-1.5">{e.model}</td>
              <td class="px-3 py-1.5 font-mono text-fg/80">{e.serial}</td>
              <td class="px-3 py-1.5 text-muted">{fmtRelative(e.installed_at)}</td>
              <td class="px-3 py-1.5"><span class={statusPill(e.status)}>{e.status}</span></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>
