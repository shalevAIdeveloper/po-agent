import { defineConfig, devices } from "@playwright/test";

const PORT = 3100;
const API_PORT = 8000;
const isCI = Boolean(process.env.CI);
// E2E_AUTH=1: also start the real API (needs local Supabase running), so the auth
// specs run end to end. Without it only the smoke specs run; auth specs skip.
const withApi = process.env.E2E_AUTH === "1";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";

export default defineConfig({
  testDir: "./e2e",
  // Run against a deployed URL only (playwright.deployed.config.ts).
  testIgnore: "deployed/**",
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  reporter: isCI ? [["github"], ["html", { open: "never" }]] : "list",
  // Server-rendered auth pages call Auth and then the API before they leave their
  // loading state; under parallel load (and on CI runners) that can exceed the
  // default 5 s. Seen once locally in 0.4c-2: /app stuck on "Loading" at 5 s.
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    ...(withApi
      ? [
          {
            command: `uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
            cwd: "../api",
            url: `http://127.0.0.1:${API_PORT}/health`,
            reuseExistingServer: !isCI,
            timeout: 120_000,
            env: {
              APP_ENV: "local",
              // Local-only po_api password from supabase/roles.sql (not a secret).
              DATABASE_URL:
                process.env.E2E_API_DATABASE_URL ??
                "postgresql://po_api:po_api_local_only@127.0.0.1:54322/postgres",
              SUPABASE_URL: supabaseUrl,
              WEB_ORIGIN: `http://localhost:${PORT}`,
            },
          },
        ]
      : []),
    {
      // CI runs `pnpm build` first and tests the production server; locally use dev.
      command: isCI ? `pnpm start --port ${PORT}` : `pnpm dev --port ${PORT}`,
      url: `http://localhost:${PORT}`,
      // Locally a leftover server on this port is reused, even if stale (README).
      reuseExistingServer: !isCI,
      timeout: 120_000,
      env: {
        // Without E2E_AUTH nothing listens here, so the smoke test sees the error state.
        NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? `http://127.0.0.1:${API_PORT}`,
        NEXT_PUBLIC_SUPABASE_URL: supabaseUrl,
        // Placeholder is enough when no auth spec runs (getClaims() makes no call without a session).
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY:
          process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "sb_publishable_placeholder",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
