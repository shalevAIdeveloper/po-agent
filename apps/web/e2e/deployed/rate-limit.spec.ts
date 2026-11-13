import { expect, type Page, test } from "@playwright/test";

/**
 * Checks on the DEPLOYED web service that the client-IP rate limit can't be dodged by
 * sending our own forwarding headers (ADR 0009). Run after a deploy that touches the
 * limiter, or after Railway networking changes (runbook railway.md, "Rate limit check"):
 *
 *   $env:DEPLOYED_WEB_URL="https://web-dev-3fce.up.railway.app"; pnpm test:deployed
 *
 * or from GitHub's network: Actions → "Deployed check" → Run workflow.
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
 * A pass proves the 11 requests shared ONE bucket, not that it is the caller's own. That
 * part comes from the logs: two runs from two networks must log two different
 * `key_tag`s in `rate_limit.refused` (runbook).
 */
const baseURL = process.env.DEPLOYED_WEB_URL;
const FAKE_TOKEN = "0".repeat(56);
const EXPIRED = "This link has expired or has already been used.";
const TOO_MANY = "Too many attempts. Wait a few minutes and try again.";
const APP_LIMIT = `${TOO_MANY} [app limit]`;

test.skip(!baseURL, "set DEPLOYED_WEB_URL");

/** One network retry: a dropped connection (mobile, CI) is not a result. */
async function openConfirmPage(page: Page): Promise<void> {
  const url = `${baseURL}/auth/confirm?type=invite&token_hash=${FAKE_TOKEN}`;
  try {
    await page.goto(url, { timeout: 45_000 });
  } catch (error) {
    if (!/net::ERR_|Timeout/.test(String(error))) throw error;
    console.log(`navigation failed once (${String(error).split("\n")[0]}); retrying`);
    await page.goto(url, { timeout: 45_000 });
  }
}

test("forged forwarding headers don't give a new rate-limit bucket", async ({ page }) => {
  test.setTimeout(240_000);
  // Printed so the run can be matched with Railway's logs (runbook).
  console.log(`run started ${new Date().toISOString()}`);
  const results: string[] = [];

  for (let i = 1; i <= 11; i++) {
    await page.setExtraHTTPHeaders({
      "X-Real-IP": `198.51.100.${i}`,
      "X-Forwarded-For": `203.0.113.${i}`,
    });
    await openConfirmPage(page);
    await page.getByRole("button", { name: /continue|accept|reset/i }).click();
    const alert = page.locator("form").getByRole("alert");
    await expect(alert).not.toBeEmpty({ timeout: 45_000 });
    const limitedByApp = (await page.locator("form").getAttribute("data-rate-limited")) === "app";
    results.push(`${(await alert.textContent()) ?? ""}${limitedByApp ? " [app limit]" : ""}`);

    if (i === 1 && results[0] === APP_LIMIT) {
      throw new Error(
        "Attempt 1 was already refused by our limiter: this IP used its budget in the last " +
          "5 minutes (an earlier run, or another device behind the same public IP, e.g. a " +
          "carrier's shared address). Wait 5 minutes and run again. This says nothing about " +
          "forged headers.",
      );
    }
  }
  console.log(`results: ${JSON.stringify(results)}`);

  // Attempts 1–10 reached Auth and were answered by it (made-up token → "expired"; an
  // Auth 429 here would mean its shared bucket is busy: wait and re-run).
  expect(results.slice(0, 10)).toEqual(Array(10).fill(EXPIRED));
  // The 11th was refused by OUR limiter (not Auth's 429, which has the same text).
  expect(results[10]).toBe(APP_LIMIT);
});
