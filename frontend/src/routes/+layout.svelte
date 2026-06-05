<script lang="ts">
  import "../app.css";
  import type { Snippet } from "svelte";
  import { page } from "$app/state";
  import { Activity, Database, FlaskConical, ShieldCheck, Sparkles, Users } from "lucide-svelte";
  import { cn } from "$lib/utils";
  import AtlasIndexHealthBadge from "$lib/components/AtlasIndexHealthBadge.svelte";
  import InspectorPanel from "$lib/components/inspector/InspectorPanel.svelte";

  let { children }: { children: Snippet } = $props();

  const nav = [
    { href: "/",          label: "Dashboard",  icon: Activity },
    { href: "/customers", label: "Customers",  icon: Users },
    { href: "/quarantine", label: "Quarantine", icon: ShieldCheck },
    { href: "/rules",     label: "Rule Studio", icon: FlaskConical },
    { href: "/features",  label: "Features",    icon: Database },
    { href: "/assist",    label: "Analyst Assist", icon: Sparkles }
  ];

  const current = $derived(page.url.pathname);
  const isActive = (href: string) =>
    href === "/" ? current === "/" : current.startsWith(href);
</script>

<div class="flex min-h-screen">
  <!-- Sidebar -->
  <aside class="hidden md:flex w-60 shrink-0 flex-col border-r border-border bg-surface/60 p-4">
    <div class="mb-6 flex items-center gap-2">
      <div class="h-8 w-8 rounded-lg bg-gradient-to-br from-accent to-accent2"></div>
      <div>
        <div class="text-sm font-semibold leading-tight">Streaming Billing</div>
        <div class="text-[11px] text-muted leading-tight">Quarantine Intelligence</div>
      </div>
    </div>
    <nav class="flex flex-col gap-1">
      {#each nav as item}
        {@const Icon = item.icon}
        <a
          href={item.href}
          class={cn(
            "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
            isActive(item.href)
              ? "bg-accent/15 text-accent border border-accent/30"
              : "text-muted hover:bg-elevated hover:text-fg"
          )}
        >
          <Icon size="16" />
          {item.label}
        </a>
      {/each}
    </nav>
    <div class="mt-auto text-[11px] text-muted">
      <div>MongoDB Atlas + ASP</div>
      <div>Voyage 4 + Bedrock Sonnet 4</div>
    </div>
  </aside>

  <!-- Main -->
  <main class="flex-1 min-w-0">
    <div class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div class="mb-4 flex items-center justify-end">
        <AtlasIndexHealthBadge />
      </div>
      {@render children()}
    </div>
  </main>

  <!-- Global MongoDB Inspector slide-over. Listens for ? globally. -->
  <InspectorPanel />
</div>
