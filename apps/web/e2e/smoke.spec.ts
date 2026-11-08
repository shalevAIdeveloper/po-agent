import { expect, test } from "@playwright/test";

// E2E_AUTH=1 starts the real API (playwright.config.ts), so the smoke test must see
// it up; without it nothing listens and the error state is the expected outcome (W2).
const apiRunning = process.env.E2E_AUTH === "1";

test("home page renders and resolves the API status", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("PO Agent");
  await expect(page.getByRole("heading", { level: 1, name: "PO Agent" })).toBeVisible();

  const up = page.getByRole("status").filter({ hasText: "API is up" });
  const down = page.getByRole("alert").filter({ hasText: "API unreachable" });
  // Longer than the API client's 5 s timeout, so a hung API still resolves (W4).
  await expect(apiRunning ? up : down).toBeVisible({ timeout: 10_000 });
});

test("unknown route shows the not-found state", async ({ page }) => {
  const response = await page.goto("/does-not-exist");

  expect(response?.status()).toBe(404);
  await expect(page.getByText("Page not found")).toBeVisible();
});
