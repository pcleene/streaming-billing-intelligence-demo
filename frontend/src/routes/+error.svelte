<script lang="ts">
  import { page } from "$app/state";
</script>

<div class="card-elevated mx-auto max-w-md p-6 text-center">
  <h1 class="text-2xl font-semibold">{page.status}</h1>
  <p class="mt-2 text-sm text-muted">{page.error?.message ?? 'Something went wrong.'}</p>
  <a class="btn btn-primary mt-4" href="/">Back to dashboard</a>
</div>
