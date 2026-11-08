import { expect, type Page, test } from "@playwright/test";

import {
  confirmPath,
  createOrganization,
  createUser,
  deleteUser,
  e2eEnv,
  emailLink,
  inviteUser,
  runAddMember,
  type TestUser,
} from "./support/supabase-admin";

const env = e2eEnv();

test.describe("invite, onboarding, reset (real Supabase + API + Mailpit)", () => {
  test.skip(env === null, "needs E2E_AUTH=1 with local Supabase (see apps/web/README.md)");

  const created: TestUser[] = [];
  test.afterAll(async () => {
    if (!env) return;
    // deleteUser removes the user's own orgs first, so the order doesn't matter.
    for (const user of created) await deleteUser(env, user);
  });

  async function invite(label: string): Promise<TestUser> {
    const user = await inviteUser(env!, label);
    created.push(user);
    return user;
  }

  const formAlert = (page: Page) => page.locator("form").getByRole("alert");

  async function choosePassword(page: Page, password: string, confirm = password) {
    await page.getByLabel("New password", { exact: true }).fill(password);
    await page.getByLabel("Confirm new password").fill(confirm);
    await page.getByRole("button", { name: "Save password" }).click();
  }

  async function signIn(page: Page, email: string, password: string) {
    await page.goto("/login");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
  }

  test("invite → continue → password → onboarding → /app → sign in again", async ({ page }) => {
    const user = await invite("invitee");
    const link = await emailLink(env!, user.email, "invite");

    await page.goto(confirmPath(link));
    await expect(page.getByRole("heading", { level: 1, name: "Accept your invitation" })).toBeVisible();
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/set-password$/);
    // Policy: 12+ characters, matching confirmation.
    await choosePassword(page, "elevenchars");
    await expect(page.getByText("Use at least 12 characters.")).toBeVisible();
    await expect(page.getByLabel("New password", { exact: true })).toBeFocused();
    await choosePassword(page, user.password, `${user.password}x`);
    await expect(page.getByText("The passwords don't match.")).toBeVisible();
    await choosePassword(page, user.password);

    await expect(page).toHaveURL(/\/onboarding$/);
    await expect(page.getByText(`Signed in as ${user.email}`)).toBeVisible();
    await page.getByLabel("Organization name").fill("Café Supply E2E");
    await expect(page.getByLabel("Short name")).toHaveValue("cafe-supply-e2e");
    const slug = `e2e-${user.id.slice(0, 8)}`;
    await page.getByLabel("Short name").fill(slug);
    await page.getByRole("button", { name: "Create organization" }).click();

    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByRole("listitem").filter({ hasText: "Café Supply E2E" })).toContainText("owner");
    // With an organization, onboarding is not shown again.
    await page.goto("/onboarding");
    await expect(page).toHaveURL(/\/app$/);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await signIn(page, user.email, user.password);
    await expect(page).toHaveURL(/\/app$/);
  });

  test("opening the link (GET) does not use it up; using it twice does", async ({ page, request }) => {
    const user = await invite("scanner");
    const link = await emailLink(env!, user.email, "invite");

    // An email security scanner fetching the link must not consume the token.
    expect((await request.get(confirmPath(link))).status()).toBe(200);
    await page.goto(confirmPath(link));
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page).toHaveURL(/\/set-password$/);

    // A second use of the same link fails with a way forward.
    await page.context().clearCookies();
    await page.goto(confirmPath(link));
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(formAlert(page)).toHaveText("This link has expired or has already been used.");
    await expect(formAlert(page)).toBeFocused();
    await expect(page.getByRole("link", { name: "request a new link" })).toBeVisible();
  });

  test("a broken link shows an error with a way forward", async ({ page }) => {
    await page.goto("/auth/confirm?type=signup&token_hash=whatever-not-valid");

    await expect(page.getByRole("heading", { level: 1, name: "This link doesn't work" })).toBeVisible();
    await expect(page.getByRole("link", { name: "request a new link" })).toBeVisible();
  });

  test("a user added to an existing org by an admin skips onboarding", async ({ page }) => {
    const owner = await createUser(env!, "existing-owner");
    created.push(owner);
    const slug = await createOrganization(env!, owner, "Shared Distributor E2E");

    const member = await invite("second-buyer");
    const output = runAddMember(env!, { email: member.email, org: slug, role: "reviewer" });
    expect(output).toContain("Audit row written");

    await page.goto(confirmPath(await emailLink(env!, member.email, "invite")));
    await page.getByRole("button", { name: "Continue" }).click();
    await choosePassword(page, member.password);

    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByRole("listitem").filter({ hasText: "Shared Distributor E2E" })).toContainText(
      "reviewer",
    );
  });

  test("a taken short name gets a field error and keeps the input", async ({ page }) => {
    const owner = await createUser(env!, "slug-owner");
    created.push(owner);
    const takenSlug = await createOrganization(env!, owner, "Taken Slug E2E");

    const user = await invite("slug-taker");
    await page.goto(confirmPath(await emailLink(env!, user.email, "invite")));
    await page.getByRole("button", { name: "Continue" }).click();
    await choosePassword(page, user.password);

    await page.getByLabel("Organization name").fill("Copycat E2E");
    await page.getByLabel("Short name").fill(takenSlug);
    await page.getByRole("button", { name: "Create organization" }).click();

    await expect(page.getByText("This address isn't available. Try another one.")).toBeVisible();
    await expect(page.getByLabel("Short name")).toBeFocused();
    await expect(page.getByLabel("Organization name")).toHaveValue("Copycat E2E");
  });

  test("password reset: email → continue → new password; the old one stops working", async ({ page }) => {
    const user = await createUser(env!, "resetter");
    created.push(user);
    await createOrganization(env!, user, "Reset Co E2E");
    const sentAfter = Date.now() - 1_000;

    await page.goto("/login");
    await page.getByRole("link", { name: "Forgot your password?" }).click();
    await page.getByLabel("Email").fill(user.email);
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.locator("form").getByRole("status").filter({ hasText: "If an account exists" })).toBeVisible();

    const link = await emailLink(env!, user.email, "recovery", { after: sentAfter });
    await page.goto(confirmPath(link));
    await expect(page.getByRole("heading", { level: 1, name: "Reset your password" })).toBeVisible();
    await page.getByRole("button", { name: "Continue" }).click();
    const newPassword = `${user.password}-new`;
    await choosePassword(page, newPassword);
    await expect(page).toHaveURL(/\/app$/);

    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await signIn(page, user.email, user.password);
    await expect(formAlert(page)).toHaveText("The email or password is incorrect.");
    await signIn(page, user.email, newPassword);
    await expect(page).toHaveURL(/\/app$/);
  });

  test("a password session alone can't change the password", async ({ page }) => {
    const user = await createUser(env!, "no-link");
    created.push(user);
    await createOrganization(env!, user, "No Link Co E2E");

    await signIn(page, user.email, user.password);
    await expect(page).toHaveURL(/\/app$/);
    await page.goto("/set-password");

    await expect(page.getByText("Open a link from your email first")).toBeVisible();
    await expect(page.getByLabel("New password", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Send me a new link" })).toBeVisible();
  });

  test("a link for another account signs the current user out and names the account", async ({ page }) => {
    const signedIn = await createUser(env!, "victim");
    created.push(signedIn);
    await createOrganization(env!, signedIn, "Victim Co E2E");
    const other = await createUser(env!, "link-owner");
    created.push(other);
    const sentAfter = Date.now() - 1_000;

    // A reset link for `other`, opened while `signedIn` is signed in.
    await page.goto("/forgot-password");
    await page.getByLabel("Email").fill(other.email);
    await page.getByRole("button", { name: "Send reset link" }).click();
    await expect(page.locator("form").getByRole("status").filter({ hasText: "If an account exists" })).toBeVisible();
    await signIn(page, signedIn.email, signedIn.password);
    await expect(page).toHaveURL(/\/app$/);

    await page.goto(confirmPath(await emailLink(env!, other.email, "recovery", { after: sentAfter })));
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page).toHaveURL(/\/set-password$/);
    await expect(page.getByText(`Setting the password for ${other.email}`)).toBeVisible();
  });

  test("reset for an unknown email gives the same answer", async ({ page }) => {
    await page.goto("/forgot-password");
    await page.getByLabel("Email").fill("nobody-at-all@e2e.test");
    await page.getByRole("button", { name: "Send reset link" }).click();

    await expect(page.locator("form").getByRole("status").filter({ hasText: "If an account exists" })).toBeVisible();
  });
});
