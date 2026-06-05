<script lang="ts">
  import type { FieldDef } from "$lib/rule-schemas";

  interface Props {
    field: FieldDef;
    value: unknown;
    onchange: (v: unknown) => void;
  }
  let { field, value, onchange }: Props = $props();

  function asNumber(s: string): number | "" {
    if (s === "") return "";
    const n = Number(s);
    return Number.isNaN(n) ? "" : n;
  }

  function listToString(arr: unknown): string {
    if (Array.isArray(arr)) return arr.join(", ");
    return String(arr ?? "");
  }
</script>

<label class="block text-sm">
  <span class="mb-1 block text-muted">{field.label}</span>

  {#if field.kind === "number"}
    <input
      class="input"
      type="number"
      step="any"
      placeholder={field.placeholder}
      value={value as number | ""}
      oninput={(e) => onchange(asNumber((e.target as HTMLInputElement).value))}
    />
  {:else if field.kind === "list_string"}
    <input
      class="input"
      type="text"
      placeholder="comma-separated"
      value={listToString(value)}
      oninput={(e) =>
        onchange(
          (e.target as HTMLInputElement).value
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        )}
    />
  {:else if field.kind === "list_number"}
    <input
      class="input"
      type="text"
      placeholder="comma-separated numbers"
      value={listToString(value)}
      oninput={(e) =>
        onchange(
          (e.target as HTMLInputElement).value
            .split(",")
            .map((s) => Number(s.trim()))
            .filter((n) => !Number.isNaN(n))
        )}
    />
  {/if}

  {#if field.help}<span class="mt-1 block text-xs text-muted/80">{field.help}</span>{/if}
</label>
