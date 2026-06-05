import { test, expect } from "@playwright/test";

/**
 * Smoke coverage for the H1 PR-13 additions on the Customer 360 page:
 *  - CustomerRefreshPanel (Refresh 360 + Refresh Metrics buttons)
 *  - TransactionPatternPanel (7/30/90 day selector)
 *  - EmbeddingStatusBadge (header)
 *
 * Requires the demo stack to be running (`make demo` + `make seed`).
 */

const SHOTS = "screenshots";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
});

async function openFirstCustomer(page: import("@playwright/test").Page) {
  await page.goto("/customers");
  await expect(page.locator("h1")).toContainText("Customers");
  const firstLink = page.locator('a[href^="/customers/"]').first();
  await firstLink.click();
  await page.waitForLoadState("networkidle");
}

test("customer 360 - refresh panel renders and reacts", async ({ page }) => {
  await openFirstCustomer(page);

  // Embedding badge should be present in the header.
  const badge = page.getByTestId("embedding-status-badge");
  await expect(badge).toBeVisible();

  // Refresh 360 button: click and verify result block updates.
  const refresh360Btn = page.getByTestId("btn-refresh-360");
  await expect(refresh360Btn).toBeVisible();
  await refresh360Btn.click();
  // Wait until the "No action yet." placeholder disappears for the 360 result card.
  const result360 = page.getByTestId("result-360");
  await expect(result360).not.toContainText("No action yet.", { timeout: 10000 });

  // Refresh Metrics button: click and verify result block updates.
  const refreshMetricsBtn = page.getByTestId("btn-refresh-metrics");
  await refreshMetricsBtn.click();
  const resultMetrics = page.getByTestId("result-metrics");
  await expect(resultMetrics).not.toContainText("No action yet.", { timeout: 10000 });

  await page.screenshot({
    path: `${SHOTS}/h1-customer-refresh-panel.png`,
    fullPage: true
  });
});

test("customer 360 - transaction pattern panel switches days", async ({ page }) => {
  await openFirstCustomer(page);

  // Default window is 30 days; switch to 7d and 90d, ensure no error and panel re-renders.
  const day7 = page.getByTestId("btn-days-7");
  const day30 = page.getByTestId("btn-days-30");
  const day90 = page.getByTestId("btn-days-90");

  await expect(day7).toBeVisible();
  await expect(day30).toBeVisible();
  await expect(day90).toBeVisible();

  await day7.click();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("section", { hasText: "Transaction pattern" })).toBeVisible();

  await day90.click();
  await page.waitForLoadState("networkidle");
  await expect(page.locator("section", { hasText: "Transaction pattern" })).toBeVisible();

  await page.screenshot({
    path: `${SHOTS}/h1-transaction-pattern-panel.png`,
    fullPage: true
  });
});
