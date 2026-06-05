<script lang="ts">
  import { severityClass } from "$lib/utils";

  interface Props {
    severity: string | undefined;
  }
  let { severity }: Props = $props();
</script>

<span class={severityClass(severity)}>
  <span class="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
  {severity ?? "—"}
</span>
