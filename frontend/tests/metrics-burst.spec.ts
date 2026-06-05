import { test, expect } from "@playwright/test";

/**
 * H4 (PR-13): smoke test for the /metrics burst detail page.
 *
 * The page must render its summary strip + sparklines + samples section
 * regardless of whether the backend has any burst data — the empty
 * envelope is a contract guarantee from the aggregator service.
 */

const SHOTS = "screenshots";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
});

test("metrics burst detail renders with empty data", async ({ page }) => {
  // Stub the burst endpoint with the documented empty envelope so the
  // test does not depend on a populated metrics_recorder.
  await page.route("**/api/metrics/burst*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: null,
        active: false,
        started_at: null,
        ended_at: null,
        rows: [],
        samples: [],
        summary: {
          row_count: 0,
          sample_count: 0,
          peak_tps: 0,
          peak_observed_tps: 0,
          mean_tps: 0,
          p99_rule_eval_ms_max: 0,
          peak_p99_ms: 0,
          target_tps_compliance: 0,
          rule_eval_p99_threshold_breaches: 0,
          started_at: null,
          ended_at: null,
          duration_seconds: 0
        }
      })
    });
  });

  await page.goto("/metrics");
  await expect(page.locator("h1")).toContainText("Burst Mode Detail");

  // Summary strip KPI labels must appear even with empty data.
  await expect(page.getByText("Peak observed TPS")).toBeVisible();
  await expect(page.getByText("Peak p99")).toBeVisible();
  await expect(page.getByText("Samples").first()).toBeVisible();

  // Empty-state copy for the table.
  await expect(page.getByText(/No burst samples yet/i)).toBeVisible();

  await page.screenshot({ path: `${SHOTS}/08-metrics-burst-empty.png`, fullPage: true });
});

test("metrics burst limit selector adapts table size", async ({ page }) => {
  // Build a small fixture of N samples; the route handler reads `limit`
  // off the query string and slices accordingly so we can verify that
  // changing the dropdown re-fetches with a smaller cap.
  function makeEnvelope(count: number) {
    const samples = Array.from({ length: count }, (_, i) => ({
      recorded_at: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString(),
      mode: "burst",
      burst_run_id: "run-test",
      observed_tps: 100 + i,
      p50_ms_ingest: 10,
      p99_ms_ingest: 80,
      rule_eval_p99_ms: 50 + i,
      quarantine_per_sec: 0,
      txns_in_window: 100,
      cases_in_window: 0
    }));
    const peakTps = Math.max(...samples.map((s) => s.observed_tps));
    const peakP99 = Math.max(...samples.map((s) => s.rule_eval_p99_ms));
    return {
      run_id: "run-test",
      active: false,
      started_at: samples[0].recorded_at,
      ended_at: samples[samples.length - 1].recorded_at,
      rows: [...samples].reverse(),
      samples,
      summary: {
        row_count: count,
        sample_count: count,
        peak_tps: peakTps,
        peak_observed_tps: peakTps,
        mean_tps: peakTps - 5,
        p99_rule_eval_ms_max: peakP99,
        peak_p99_ms: peakP99,
        target_tps_compliance: 1,
        rule_eval_p99_threshold_breaches: 0,
        started_at: samples[0].recorded_at,
        ended_at: samples[samples.length - 1].recorded_at,
        duration_seconds: count - 1
      }
    };
  }

  await page.route("**/api/metrics/burst*", async (route) => {
    const url = new URL(route.request().url());
    const lim = Math.max(1, Math.min(720, Number(url.searchParams.get("limit") ?? 240)));
    // Cap the fixture to a small size (12) regardless of the requested
    // limit when limit >= 12, otherwise return exactly `lim` samples.
    const count = Math.min(lim, 12);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeEnvelope(count))
    });
  });

  await page.goto("/metrics");
  await expect(page.locator("h1")).toContainText("Burst Mode Detail");

  // Initial fetch (limit=240) -> 12 rows in fixture.
  await expect(page.locator("table tbody tr")).toHaveCount(12);

  // Switch the limit selector to 60 — the route handler will return 12
  // (still capped under 60) but importantly verifies the page re-fetches
  // and the URL contains limit=60.
  let observedLimit: string | null = null;
  page.on("request", (req) => {
    const u = req.url();
    if (u.includes("/api/metrics/burst")) {
      const sp = new URL(u).searchParams.get("limit");
      if (sp) observedLimit = sp;
    }
  });

  // Change to 60 and verify request issued with limit=60.
  await page.selectOption('select', "60");
  await page.waitForResponse((res) => res.url().includes("/api/metrics/burst"));
  expect(observedLimit).toBe("60");
  await expect(page.locator("table tbody tr")).toHaveCount(12);

  // Now request a tiny limit by directly issuing a refresh after stubbing
  // a smaller count. We re-route to return 3 samples for any subsequent
  // call so the table visibly shrinks.
  await page.unroute("**/api/metrics/burst*");
  await page.route("**/api/metrics/burst*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeEnvelope(3))
    });
  });

  await page.getByRole("button", { name: /Refresh/i }).click();
  await page.waitForResponse((res) => res.url().includes("/api/metrics/burst"));
  await expect(page.locator("table tbody tr")).toHaveCount(3);
});
