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
 *
 * A pass proves the 11 requests shared ONE bucket, not that it is the caller's own: if
 * the edge stopped sending X-Real-IP, everyone would share the "unknown" bucket and this
 * would still pass. The runbook adds the two checks for that: no `rate_limit.ip_unknown`
 * in the web logs, and a second run from another network starts fresh.
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
    const limitedByApp = (await page.locator("form").getAttribute("data-rate-limited")) === "app";
    results.push(`${(await alert.textContent()) ?? ""}${limitedByApp ? " [app limit]" : ""}`);
  }

  // Attempts 1–10 reached Auth and were answered by it (made-up token → "expired"; an
  // Auth 429 here would mean its shared bucket is busy: wait and re-run).
  expect(results.slice(0, 10)).toEqual(Array(10).fill("This link has expired or has already been used."));
  // The 11th was refused by OUR limiter (not Auth's 429, which has the same text).
  expect(results[10]).toBe(`${TOO_MANY} [app limit]`);
});
