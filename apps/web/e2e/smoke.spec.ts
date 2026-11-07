import { expect, test } from "@playwright/test";

test("home page renders and resolves the API status", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("PO Agent");
  await expect(page.getByRole("heading", { level: 1, name: "PO Agent" })).toBeVisible();

  // Either outcome is fine for a smoke test; what matters is that the page
  // leaves the loading state and renders a real status.
  const resolved = page
    .getByRole("status")
    .filter({ hasText: "API is up" })
    .or(page.getByRole("alert").filter({ hasText: "API unreachable" }));
  await expect(resolved).toBeVisible();
});

test("unknown route shows the not-found state", async ({ page }) => {
  const response = await page.goto("/does-not-exist");

  expect(response?.status()).toBe(404);
  await expect(page.getByText("Page not found")).toBeVisible();
});
