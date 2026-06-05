import { test, expect } from "@playwright/test";

/**
 * H2 (PR-13): Feature drift detail page + snapshot section + investigate action.
 *
 * The backend may not always be running in CI, so most assertions are about
 * the page rendering its skeleton/error states correctly. Form interaction
 * is exercised but submission is only attempted when the snapshot section
 * yielded at least one feature row to click.
 */

test("features index renders drift snapshot section and links to detail", async ({ page }) => {
  await page.goto("/features");
  await expect(page.locator("h1")).toContainText("Feature");

  // The snapshot section heading should always render.
  await expect(page.getByText("Drift snapshot")).toBeVisible();

  // Wait for either the table or an error/empty state to settle.
  const table = page.locator('[data-testid="drift-snapshot"]');
  const loading = page.locator('[data-testid="drift-snapshot-loading"]');
  await Promise.race([
    table.waitFor({ state: "visible", timeout: 10000 }).catch(() => null),
    loading
      .waitFor({ state: "detached", timeout: 10000 })
      .catch(() => null)
  ]);

  // If a feature row exists, click it and verify detail page.
  const firstLink = page.locator('a[href^="/features/"]').first();
  if (await firstLink.count()) {
    await firstLink.click();
    await expect(page.locator("h1")).toContainText("Feature:");
    await expect(page.getByText("Drift status")).toBeVisible();
    await expect(page.getByText("Impact analysis")).toBeVisible();
    await expect(page.getByText("Investigate action")).toBeVisible();
  }
});

test("feature detail page renders sections and exercises investigate form", async ({ page }) => {
  // Use a synthetic feature name; backend may 404 but UI must still render.
  await page.goto("/features/txn_count_5m");
  await expect(page.locator("h1")).toContainText("Feature:");

  await expect(page.getByText("Drift status")).toBeVisible();
  await expect(page.getByText("Impact analysis")).toBeVisible();
  await expect(page.getByText("Investigate action")).toBeVisible();

  // Form interaction without submitting against a possibly-down backend.
  const actionSelect = page.locator("#ia-action");
  await expect(actionSelect).toBeVisible();

  // Snooze input should be disabled until action=snooze.
  const snoozeInput = page.locator("#ia-snooze");
  await expect(snoozeInput).toBeDisabled();

  await actionSelect.selectOption("snooze");
  await expect(snoozeInput).toBeEnabled();

  await page.locator("#ia-note").fill("Investigating possible upstream drift.");

  // Switch back to acknowledge to leave form in a benign state.
  await actionSelect.selectOption("acknowledge");
  await expect(snoozeInput).toBeDisabled();
});
