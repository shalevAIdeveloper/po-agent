import { defineConfig, devices } from "@playwright/test";

// Checks against a deployed environment (`pnpm test:deployed`); no local servers.
// Never part of CI: they need the real edge in front of the app.
export default defineConfig({
  testDir: "./e2e/deployed",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  expect: { timeout: 15_000 },
  use: { trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
