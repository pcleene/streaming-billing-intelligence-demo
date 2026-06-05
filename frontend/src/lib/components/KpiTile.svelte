<script lang="ts">
  interface Props {
    label: string;
    value: string | number;
    unit?: string;
    sub?: string;
    accent?: "default" | "ok" | "warn" | "danger" | "accent";
  }
  let { label, value, unit = "", sub = "", accent = "default" }: Props = $props();

  const accentClass = $derived(
    accent === "ok"
      ? "text-ok"
      : accent === "warn"
        ? "text-warn"
        : accent === "danger"
          ? "text-danger"
          : accent === "accent"
            ? "text-accent"
            : "text-fg"
  );
</script>

<div class="card p-4">
  <div class="text-xs uppercase tracking-wide text-muted">{label}</div>
  <div class="mt-1 flex items-baseline gap-1">
    <div class="text-2xl font-semibold {accentClass}">{value}</div>
    {#if unit}<div class="text-sm text-muted">{unit}</div>{/if}
  </div>
  {#if sub}<div class="mt-1 text-xs text-muted">{sub}</div>{/if}
</div>
