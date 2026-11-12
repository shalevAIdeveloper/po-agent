import { randomInt } from "node:crypto";

import { test as base } from "@playwright/test";

/**
 * `test` with its own client IP per test: every browser request carries a random
 * `X-Real-IP` (what Railway's edge sets in the cloud). The auth Server Actions limit
 * per client IP (A13, ADR 0009), so without this every test in a run would share one
 * bucket and the suite would trip its own limits.
 */
export const test = base.extend({
  // `provide`, not Playwright's usual `use`: the React hooks lint rule flags `use(...)`.
  extraHTTPHeaders: async ({ extraHTTPHeaders }, provide) => {
    await provide({ ...extraHTTPHeaders, "X-Real-IP": randomTestIp() });
  },
});

/** A random address in 10.0.0.0/8 (private; collisions between tests are negligible). */
export function randomTestIp(): string {
  return `10.${randomInt(256)}.${randomInt(256)}.${randomInt(1, 255)}`;
}

export { expect } from "@playwright/test";
export type { Page } from "@playwright/test";
