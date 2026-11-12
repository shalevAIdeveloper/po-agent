import "server-only";

import { createHmac, randomBytes } from "node:crypto";
import { headers } from "next/headers";

import { type ClientIp, parseClientIp } from "@/lib/rate-limit/client-ip";
import { type RateRule, SlidingWindowLimiter } from "@/lib/rate-limit/limiter";

/**
 * App-level limits for the auth Server Actions (A13, ADR 0009). Supabase Auth sees the
 * web server's IP for all of them, so its own per-IP limit is one bucket shared by every
 * user; these limits key on the real client IP and on the account instead.
 */

const MINUTE = 60_000;

export const AUTH_RATE_RULES = {
  /** Sign-in attempts per client IP (IPv4 address or IPv6 /64). */
  loginIp: { limit: 10, windowMs: 5 * MINUTE },
  /** Sign-in attempts per IPv6 /48. */
  loginIpWide: { limit: 30, windowMs: 5 * MINUTE },
  /** Sign-in attempts per account that didn't succeed; cleared by a successful sign-in. */
  loginEmailFailures: { limit: 5, windowMs: 15 * MINUTE },
  /** Email-link verifications (`/auth/confirm` POST) per client IP. */
  confirmIp: { limit: 10, windowMs: 5 * MINUTE },
  /** Email-link verifications per IPv6 /48. */
  confirmIpWide: { limit: 30, windowMs: 5 * MINUTE },
  /** Password-reset requests per client IP. */
  resetIp: { limit: 5, windowMs: 15 * MINUTE },
  /** Password-reset requests per IPv6 /48. */
  resetIpWide: { limit: 15, windowMs: 15 * MINUTE },
  /** Password-reset requests per address. */
  resetEmail: { limit: 3, windowMs: 60 * MINUTE },
} as const satisfies Record<string, RateRule>;

type RuleName = keyof typeof AUTH_RATE_RULES;
const limiters = Object.fromEntries(
  Object.entries(AUTH_RATE_RULES).map(([name, rule]) => [name, new SlidingWindowLimiter(rule)]),
) as Record<RuleName, SlidingWindowLimiter>;

// Emails are keyed by HMAC so the limiter never holds an address (PII). The key is
// random per process: the counters live in this process too, so nothing needs to agree
// on it across restarts or services, and there is no secret to configure or leak.
const EMAIL_KEY = randomBytes(32);

/** Account key: HMAC-SHA256 of the trimmed, lower-cased address. */
export function emailKey(email: string): string {
  return createHmac("sha256", EMAIL_KEY).update(email.trim().toLowerCase()).digest("base64url");
}

/** Requests without a trusted client IP all share this key. */
const UNKNOWN_IP: ClientIp = { key: "unknown", wide: null };
let lastUnknownLog = 0;

/**
 * The caller's IP keys. Without a trusted IP the request goes to one shared bucket and
 * an event is logged (at most once a minute, without the header's value): on Railway
 * every public request has `X-Real-IP`, so this event means the edge contract changed
 * and every user now shares one bucket (runbook railway.md, "Rate limit check").
 */
export async function requestIp(): Promise<ClientIp> {
  const ip = parseClientIp(await headers());
  if ("key" in ip) return ip;

  const now = Date.now();
  if (now - lastUnknownLog >= MINUTE) {
    lastUnknownLog = now;
    console.warn(JSON.stringify({ level: "warning", event: "rate_limit.ip_unknown", header: ip.reason }));
  }
  return UNKNOWN_IP;
}

/** One attempt against an action's IP limits (the address or /64, then the /48). */
function takeIp(narrow: RuleName, wide: RuleName, ip: ClientIp): boolean {
  if (!limiters[narrow].take(ip.key)) return false;
  return ip.wide === null || limiters[wide].take(ip.wide);
}

/**
 * Counts a sign-in attempt; false if this IP or this account must wait.
 *
 * The account slot is reserved here, before Auth is called, so parallel requests can't
 * all pass the check before any failure is recorded. Afterwards the caller either keeps
 * it (wrong credentials), clears the account (success: `recordLoginSuccess`) or gives it
 * back (`releaseLoginAttempt`: Auth was down or rate-limited, which isn't the account's
 * fault).
 */
export function allowLogin(ip: ClientIp, email: string): boolean {
  // The IP attempt is counted even when the account is locked.
  if (!takeIp("loginIp", "loginIpWide", ip)) return false;
  return limiters.loginEmailFailures.take(emailKey(email));
}

/** Signed in: earlier failures no longer count. */
export function recordLoginSuccess(email: string): void {
  limiters.loginEmailFailures.reset(emailKey(email));
}

/** The attempt didn't test the credentials (Auth outage or Auth's own 429). */
export function releaseLoginAttempt(email: string): void {
  limiters.loginEmailFailures.release(emailKey(email));
}

/** Counts an email-link verification; false if this IP must wait. */
export function allowEmailLinkCheck(ip: ClientIp): boolean {
  return takeIp("confirmIp", "confirmIpWide", ip);
}

/** Counts a reset request; false if this IP or this address must wait. */
export function allowPasswordReset(ip: ClientIp, email: string): boolean {
  // IP first: a blocked IP must not use up the address's budget.
  if (!takeIp("resetIp", "resetIpWide", ip)) return false;
  return limiters.resetEmail.take(emailKey(email));
}
