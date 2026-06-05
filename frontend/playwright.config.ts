import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.ACME_BASE_URL ?? "http://localhost:5173",
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
    trace: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
