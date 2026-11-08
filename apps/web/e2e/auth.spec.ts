import { expect, type Page, test } from "@playwright/test";

import {
  createOrganization,
  createUser,
  deleteUser,
  e2eEnv,
  type TestUser,
} from "./support/supabase-admin";

const env = e2eEnv();
const ORG_NAME = "Acme Supply E2E";

test.describe("auth (real Supabase + API)", () => {
  test.skip(env === null, "needs E2E_AUTH=1 with local Supabase (see apps/web/README.md)");

  let owner: TestUser;
  let orgless: TestUser;

  test.beforeAll(async () => {
    if (!env) return;
    owner = await createUser(env, "owner");
    await createOrganization(env, owner, ORG_NAME);
    orgless = await createUser(env, "orgless");
  });

  test.afterAll(async () => {
    if (!env) return;
    await Promise.all([owner && deleteUser(env, owner), orgless && deleteUser(env, orgless)]);
  });

  // Scoped to the form: Next's route announcer is also role="alert".
  const formAlert = (page: Page) => page.locator("form").getByRole("alert");

  async function signIn(page: Page, user: TestUser, password = user.password) {
    await page.getByLabel("Email").fill(user.email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
  }

  test("a guest is sent from /app to /login with `next`", async ({ page }) => {
    await page.goto("/app");

    await expect(page).toHaveURL(/\/login\?next=%2Fapp$/);
    await expect(page.getByRole("heading", { level: 1, name: "Sign in to PO Agent" })).toBeVisible();
  });

  test("a wrong password gets one generic error and keeps the email", async ({ page }) => {
    await page.goto("/login");
    await signIn(page, owner, "definitely-not-the-password");

    await expect(formAlert(page)).toHaveText("The email or password is incorrect.");
    // Focus moves to the message, and both fields point at it.
    await expect(formAlert(page)).toBeFocused();
    await expect(page.getByLabel("Password")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Password")).toHaveAttribute("aria-describedby", "form-error");
    await expect(page.getByLabel("Email")).toHaveValue(owner.email);
    await expect(page.getByLabel("Password")).toHaveValue("");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("an unknown email gets the same error", async ({ page }) => {
    await page.goto("/login");
    await signIn(page, { ...owner, email: "nobody-here@e2e.test" });

    await expect(formAlert(page)).toHaveText("The email or password is incorrect.");
  });

  test("empty fields are reported per field", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByLabel("Email")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Email")).toBeFocused();
    await expect(page.getByText("Enter a valid email address.")).toBeVisible();
    await expect(page.getByText("Enter your password.")).toBeVisible();
  });

  test("sign in → /app shows the org → session survives reload → sign out", async ({ page }) => {
    await page.goto("/app");
    await signIn(page, owner);

    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByText(`Signed in as ${owner.email}`)).toBeVisible();
    await expect(page.getByRole("listitem").filter({ hasText: ORG_NAME })).toContainText("owner");

    await page.reload();
    await expect(page.getByText(ORG_NAME)).toBeVisible();

    // A signed-in user never sees the login page.
    await page.goto("/login");
    await expect(page).toHaveURL(/\/app$/);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);

    await page.goto("/app");
    await expect(page).toHaveURL(/\/login\?next=%2Fapp$/);
  });

  test("a user without an organization is sent to onboarding", async ({ page }) => {
    await page.goto("/login");
    await signIn(page, orgless);

    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByRole("heading", { level: 1, name: "Set up your organization" })).toBeVisible();
    await expect(page).toHaveTitle("Set up your organization · PO Agent");
  });

  test("an external `next` is ignored after sign in", async ({ page }) => {
    await page.goto("/login?next=//evil.example/steal");
    await signIn(page, owner);

    await expect(page).toHaveURL(/^http:\/\/localhost:3100\/app$/);
  });
});
