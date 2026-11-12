import { expect, test } from "@playwright/test";

/**
 * Checks on the DEPLOYED web service that the client-IP rate limit can't be dodged by
 * sending our own forwarding headers (ADR 0009). Run by hand after a deploy that touches
 * the limiter, or after Railway networking changes:
 *
 *   $env:DEPLOYED_WEB_URL="https://web-dev-3fce.up.railway.app"; pnpm test:deployed
 *
 * It opens /auth/confirm with a made-up token 11 times, each time with a different
 * forged X-Real-IP / X-Forwarded-For. If Railway's edge overwrites those headers (as
 * documented), all 11 land in the caller's real-IP bucket and the 11th is refused
 * without reaching Auth. If the forged values were trusted, every attempt would get a
 * fresh bucket and the 11th would say "expired" instead.
 *
 * No account is needed and no email is sent; Auth sees 10 failed verifications.
 * Afterwards the caller's IP is limited on /auth/confirm for 5 minutes.
 */
const baseURL = process.env.DEPLOYED_WEB_URL;
const FAKE_TOKEN = "0".repeat(56);
const TOO_MANY = "Too many attempts. Wait a few minutes and try again.";

test.skip(!baseURL, "set DEPLOYED_WEB_URL");

test("forged forwarding headers don't give a new rate-limit bucket", async ({ page }) => {
  test.setTimeout(120_000);
  const results: string[] = [];

  for (let i = 1; i <= 11; i++) {
    await page.setExtraHTTPHeaders({
      "X-Real-IP": `198.51.100.${i}`,
      "X-Forwarded-For": `203.0.113.${i}`,
    });
    await page.goto(`${baseURL}/auth/confirm?type=invite&token_hash=${FAKE_TOKEN}`);
    await page.getByRole("button", { name: /continue|accept|reset/i }).click();
    const alert = page.locator("form").getByRole("alert");
    await expect(alert).not.toBeEmpty();
    results.push((await alert.textContent()) ?? "");
  }

  // Attempts 1–10 reached Auth (made-up token → "expired"); the 11th must not.
  expect(results.slice(0, 10).every((text) => !text.includes("Too many"))).toBe(true);
  expect(results[10]).toBe(TOO_MANY);
});
