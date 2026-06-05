import { test, expect } from "@playwright/test";

/**
 * Captures the canonical demo screenshots used in docs/ and the README.
 * Run with: `npx playwright test --project=chromium tests/screenshots.spec.ts`
 *
 * Requires the demo stack to be running (`make demo` + `make seed`).
 * Output lands under `frontend/screenshots/`.
 */

const SHOTS = "screenshots";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
});

test("01 overview", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("h1")).toContainText("Overview");
  // Wait for at least one KPI tile + SSE to populate.
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/01-overview.png`, fullPage: true });
});

test("02 customer-360", async ({ page }) => {
  await page.goto("/customers");
  await expect(page.locator("h1")).toContainText("Customers");
  const firstLink = page.locator('a[href^="/customers/"]').first();
  await firstLink.click();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/02-customer-360.png`, fullPage: true });
});

test("03 rule-studio", async ({ page }) => {
  await page.goto("/rules");
  await expect(page.locator("h1")).toContainText("Rule Studio");
  await page.screenshot({ path: `${SHOTS}/03-rule-studio.png`, fullPage: true });
});

test("04 quarantine-queue", async ({ page }) => {
  await page.goto("/quarantine");
  await expect(page.locator("h1")).toContainText("Quarantine");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/04-quarantine-queue.png`, fullPage: true });
});

test("05 case-detail-with-assist", async ({ page }) => {
  await page.goto("/quarantine");
  const firstCase = page.locator('a[href^="/quarantine/"]').first();
  await firstCase.click();
  await page.waitForLoadState("networkidle");
  // Trigger AI assist
  const assistBtn = page.getByRole("button", { name: /Ask AI Assist/i });
  if (await assistBtn.isEnabled()) {
    await assistBtn.click();
    await page.waitForTimeout(8000); // Bedrock round-trip
  }
  await page.screenshot({ path: `${SHOTS}/05-case-with-assist.png`, fullPage: true });
});

test("06 features", async ({ page }) => {
  await page.goto("/features");
  await expect(page.locator("h1")).toContainText("Feature");
  await page.screenshot({ path: `${SHOTS}/06-features.png`, fullPage: true });
});

test("07 assist-queue", async ({ page }) => {
  await page.goto("/assist");
  await expect(page.locator("h1")).toContainText("Analyst assist");
  await page.screenshot({ path: `${SHOTS}/07-assist-queue.png`, fullPage: true });
});
