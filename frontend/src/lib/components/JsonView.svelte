<script lang="ts">
  interface Props { value: unknown; }
  let { value }: Props = $props();
  const formatted = $derived(JSON.stringify(value, null, 2));
</script>

<pre class="card max-h-72 overflow-auto p-3 text-xs leading-relaxed font-mono text-fg/90">{formatted}</pre>
