<script lang="ts">
  import { onMount } from "svelte";
  import { rulesApi } from "$lib/api";
  import type { Rule, RuleMode } from "$lib/types";
  import Section from "$lib/components/Section.svelte";
  import ParamField from "$lib/components/ParamField.svelte";
  import { RULE_SCHEMAS, defaultParams } from "$lib/rule-schemas";
  import { cn } from "$lib/utils";
  import InspectorTrigger from "$lib/components/inspector/InspectorTrigger.svelte";
  import { inspector } from "$lib/components/inspector/stores/inspector.svelte";

  let rules = $state<Rule[]>([]);
  let selected = $state<Rule | null>(null);
  let testRuleType = $state<string>(RULE_SCHEMAS[0].rule_type);
  let testParams = $state<Record<string, unknown>>(defaultParams(RULE_SCHEMAS[0].rule_type));
  let testSampleSize = $state(1000);
  let testResult = $state<{ hit_count: number; hit_rate: number; sample_size: number; hits: unknown[] } | null>(null);
  let testing = $state(false);

  async function reload() {
    const r = await rulesApi.list({ inspect: inspector.open });
    rules = r.items;
    if (selected) selected = rules.find((x) => x.rule_id === selected!.rule_id) ?? null;
  }

  $effect(() => { if (inspector.open) reload(); });

  async function setMode(rule: Rule, mode: RuleMode) {
    await rulesApi.setMode(rule.rule_id, mode);
    await reload();
  }

  function chooseRuleType(rt: string) {
    testRuleType = rt;
    testParams = defaultParams(rt);
    testResult = null;
  }

  function loadParamsFromRule(r: Rule) {
    testRuleType = r.rule_type;
    testParams = { ...(r.parameters as Record<string, unknown>) };
    testResult = null;
  }

  async function runTest() {
    testing = true;
    try {
      testResult = await rulesApi.test(testRuleType, testParams, testSampleSize);
    } finally {
      testing = false;
    }
  }

  const currentSchema = $derived(RULE_SCHEMAS.find((s) => s.rule_type === testRuleType));

  onMount(reload);
</script>

<div class="space-y-6">
  <header>
    <div class="flex items-center gap-2">
      <h1 class="text-2xl font-semibold">Rule Studio</h1>
      <InspectorTrigger hint="rules.find" />
    </div>
    <p class="text-sm text-muted">Edit, shadow-test, and activate quarantine rules.</p>
  </header>

  <Section title="Active rules" subtitle={`${rules.length} configured`}>
    <div class="overflow-hidden rounded-lg border border-border">
      <table class="w-full text-left text-sm">
        <thead class="bg-elevated text-xs uppercase tracking-wide text-muted">
          <tr>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2">Severity</th>
            <th class="px-3 py-2">Mode</th>
            <th class="px-3 py-2 text-right">Hits</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {#each rules as r}
            <tr class="border-t border-border hover:bg-elevated/60">
              <td class="px-3 py-2">{r.name}</td>
              <td class="px-3 py-2 font-mono text-xs text-muted">{r.rule_type}</td>
              <td class="px-3 py-2"><span class="pill pill-muted">{r.severity}</span></td>
              <td class="px-3 py-2">
                <div class="inline-flex overflow-hidden rounded-md border border-border text-xs">
                  {#each ['shadow','active','disabled'] as m}
                    <button
                      class={cn(
                        'px-2 py-1',
                        r.mode === m
                          ? 'bg-accent/20 text-accent'
                          : 'text-muted hover:bg-elevated'
                      )}
                      onclick={() => setMode(r, m as RuleMode)}>
                      {m}
                    </button>
                  {/each}
                </div>
              </td>
              <td class="px-3 py-2 text-right">{r.hit_count}</td>
              <td class="px-3 py-2 text-right">
                <button class="btn" onclick={() => loadParamsFromRule(r)}>Test</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </Section>

  <Section title="Test against historical sample" subtitle="Same aggregation pipeline used in ASP">
    {#snippet actions()}
      <button class="btn btn-primary" onclick={runTest} disabled={testing}>
        {testing ? "Running…" : "Run"}
      </button>
    {/snippet}

    <div class="grid gap-4 sm:grid-cols-3">
      <label class="block text-sm">
        <span class="mb-1 block text-muted">Rule type</span>
        <select class="input" value={testRuleType} onchange={(e) => chooseRuleType((e.target as HTMLSelectElement).value)}>
          {#each RULE_SCHEMAS as s}<option value={s.rule_type}>{s.label}</option>{/each}
        </select>
      </label>
      <label class="block text-sm">
        <span class="mb-1 block text-muted">Sample size</span>
        <input class="input" type="number" min="10" max="10000" bind:value={testSampleSize} />
      </label>
    </div>

    {#if currentSchema?.fields?.length}
      <div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {#each currentSchema.fields as f}
          <ParamField field={f} value={testParams[f.key]} onchange={(v) => (testParams = { ...testParams, [f.key]: v })} />
        {/each}
      </div>
    {/if}

    {#if testResult}
      <div class="mt-5 rounded-lg border border-border bg-elevated p-4">
        <div class="grid gap-4 sm:grid-cols-3">
          <div>
            <div class="text-xs uppercase text-muted">Hits</div>
            <div class="text-2xl font-semibold text-accent">{testResult.hit_count}</div>
          </div>
          <div>
            <div class="text-xs uppercase text-muted">Hit rate</div>
            <div class="text-2xl font-semibold">{(testResult.hit_rate * 100).toFixed(2)}%</div>
          </div>
          <div>
            <div class="text-xs uppercase text-muted">Sample</div>
            <div class="text-2xl font-semibold">{testResult.sample_size.toLocaleString()}</div>
          </div>
        </div>
        {#if testResult.hits.length}
          <details class="mt-3">
            <summary class="cursor-pointer text-sm text-muted">Top hits</summary>
            <pre class="mt-2 max-h-72 overflow-auto rounded-md bg-bg/60 p-3 text-xs">{JSON.stringify(testResult.hits, null, 2)}</pre>
          </details>
        {/if}
      </div>
    {/if}
  </Section>
</div>
