import { test, expect } from "@playwright/test";

/**
 * H3 (PR-13) — agent trace viewer + batch ai-assist console.
 *
 * Run with: `npx playwright test --project=chromium tests/agent-trace.spec.ts`
 * Requires the demo stack to be running on the configured baseURL.
 */

const SHOTS = "screenshots";
const MAX_IDS = 25;

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
});

test("batch-assist counter and submit gating", async ({ page }) => {
  await page.goto("/quarantine/batch-assist");
  await expect(page.locator("h1")).toContainText("Batch AI assist");

  const submit = page.getByTestId("batch-submit");
  const pill = page.getByTestId("id-count-pill");

  // Empty state — submit disabled, count "0 ids".
  await expect(submit).toBeDisabled();
  await expect(pill).toContainText("0 ids");

  // Add a few ids — submit enabled, pill not red.
  const textarea = page.locator("textarea");
  await textarea.fill(["case_a", "case_b", "case_c"].join("\n"));
  await expect(pill).toContainText("3 ids");
  await expect(submit).toBeEnabled();
  // Should not have the danger pill class yet.
  await expect(pill).not.toHaveClass(/pill-danger/);

  // Push past the cap — pill turns red ("pill-danger") and submit disables.
  const tooMany = Array.from({ length: MAX_IDS + 5 }, (_, i) => `case_${i}`).join("\n");
  await textarea.fill(tooMany);
  await expect(pill).toContainText(`${MAX_IDS + 5} ids`);
  await expect(pill).toHaveClass(/pill-danger/);
  await expect(submit).toBeDisabled();

  await page.screenshot({ path: `${SHOTS}/h3-batch-assist.png`, fullPage: true });
});

test("agent trace panel renders on case detail without crashing", async ({ page }) => {
  await page.goto("/quarantine");
  const firstCase = page.locator('a[href^="/quarantine/"]').first();
  // If no cases exist, just hit a synthetic case id; the panel should still
  // render (collapsed) without throwing.
  if ((await firstCase.count()) > 0) {
    await firstCase.click();
  } else {
    await page.goto("/quarantine/case_demo");
  }
  await page.waitForLoadState("networkidle");

  // The Agent trace section header should be present (collapsed by default).
  await expect(page.getByRole("heading", { name: /agent trace/i })).toBeVisible();

  await page.screenshot({ path: `${SHOTS}/h3-case-with-agent-trace.png`, fullPage: true });
});
