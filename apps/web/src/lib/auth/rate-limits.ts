import "server-only";

import { createHmac, randomBytes } from "node:crypto";
import { headers } from "next/headers";

import { clientIpKey } from "@/lib/rate-limit/client-ip";
import { type RateRule, SlidingWindowLimiter } from "@/lib/rate-limit/limiter";

/**
 * App-level limits for the auth Server Actions (A13, ADR 0009). Supabase Auth sees the
 * web server's IP for all of them, so its own per-IP limit is one bucket shared by every
 * user; these limits key on the real client IP and on the account instead.
 */

const MINUTE = 60_000;

export const AUTH_RATE_RULES = {
  /** Sign-in attempts per client IP. */
  loginIp: { limit: 10, windowMs: 5 * MINUTE },
  /** Failed sign-ins per account; cleared by a successful sign-in. */
  loginEmailFailures: { limit: 5, windowMs: 15 * MINUTE },
  /** Email-link verifications (`/auth/confirm` POST) per client IP. */
  confirmIp: { limit: 10, windowMs: 5 * MINUTE },
  /** Password-reset requests per client IP. */
  resetIp: { limit: 5, windowMs: 15 * MINUTE },
  /** Password-reset requests per address. */
  resetEmail: { limit: 3, windowMs: 60 * MINUTE },
} as const satisfies Record<string, RateRule>;

const limiters = {
  loginIp: new SlidingWindowLimiter(AUTH_RATE_RULES.loginIp),
  loginEmailFailures: new SlidingWindowLimiter(AUTH_RATE_RULES.loginEmailFailures),
  confirmIp: new SlidingWindowLimiter(AUTH_RATE_RULES.confirmIp),
  resetIp: new SlidingWindowLimiter(AUTH_RATE_RULES.resetIp),
  resetEmail: new SlidingWindowLimiter(AUTH_RATE_RULES.resetEmail),
};

// Emails are keyed by HMAC so the limiter never holds an address (PII). The key is
// random per process: the counters live in this process too, so nothing needs to agree
// on it across restarts or services, and there is no secret to configure or leak.
const EMAIL_KEY = randomBytes(32);

/** Account key: HMAC-SHA256 of the trimmed, lower-cased address. */
export function emailKey(email: string): string {
  return createHmac("sha256", EMAIL_KEY).update(email.trim().toLowerCase()).digest("base64url");
}

/** The caller's IP key; requests without a trusted IP share one bucket. */
export async function requestIpKey(): Promise<string> {
  return clientIpKey(await headers()) ?? "unknown";
}

/** Counts a sign-in attempt; false if this IP or this account must wait. */
export function allowLogin(ip: string, email: string): boolean {
  // The IP attempt is counted even when the account is locked.
  if (!limiters.loginIp.take(ip)) return false;
  return !limiters.loginEmailFailures.isLimited(emailKey(email));
}

/** Wrong credentials for this account. */
export function recordLoginFailure(email: string): void {
  limiters.loginEmailFailures.hit(emailKey(email));
}

/** Signed in: earlier failures no longer count. */
export function recordLoginSuccess(email: string): void {
  limiters.loginEmailFailures.reset(emailKey(email));
}

/** Counts an email-link verification; false if this IP must wait. */
export function allowEmailLinkCheck(ip: string): boolean {
  return limiters.confirmIp.take(ip);
}

/** Counts a reset request; false if this IP or this address must wait. */
export function allowPasswordReset(ip: string, email: string): boolean {
  if (!limiters.resetIp.take(ip)) return false;
  return limiters.resetEmail.take(emailKey(email));
}
