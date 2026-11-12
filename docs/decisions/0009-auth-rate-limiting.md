# 0009 — Rate limiting for sign-in, email links and password reset

## Context
Sign-in, email-link verification (`/auth/confirm`) and password reset run as Server Actions in `apps/web` (ADR 0007), so Supabase Auth sees the web server's IP for every user. Its per-IP limits (`sign_in_sign_ups` 30 / 5 min, `token_verifications` 30 / 5 min) are one bucket shared by all users: anyone could lock everyone out, and nothing limits guesses against one account (`sec` M, 0.4c-1 and 0.4c-2; Follow-up A13, a pilot gate).

## Decision
- **Limits in the web app**, checked at the start of each action, before Auth is called. Over a limit, Auth is not called:

  | Action | Key | Limit | Answer when over |
  |---|---|---|---|
  | Sign-in | client IP | 10 attempts / 5 min | "Too many sign-in attempts…" (the message Auth's 429 already gets) |
  | Sign-in | account | 5 **failed** attempts / 15 min, cleared by a successful sign-in | same |
  | `/auth/confirm` (POST) | client IP | 10 / 5 min | "Too many attempts…" (existing message) |
  | Reset request | client IP | 5 / 15 min | the usual neutral "If an account exists…" notice, no email sent |
  | Reset request | address | 3 / 60 min | same |

  Only a rejected email/password counts as a failure (4xx other than 429); Auth outages and Auth's own 429 don't. Malformed input and invalid link types are refused before the limiter and don't use it up. Sliding window; a blocked attempt is not recorded. The values live in `AUTH_RATE_RULES` (`apps/web/src/lib/auth/rate-limits.ts`), pinned by a unit test against this table.
- **Client IP = `X-Real-IP` only.** Railway documents `X-Real-IP` as the client's address, and its edge overwrites `X-Real-IP` / `X-Forwarded-For` sent by the client on public traffic (custom domains and `*.up.railway.app`). `X-Forwarded-For` is never read: its left part is client-controlled. A missing, comma-joined (duplicated) or malformed value → one shared `unknown` bucket, never "no limit". IPv6 is keyed by its /64; IPv4-mapped IPv6 as the IPv4 address. Railway calls the overwrite current behavior, not a versioned contract, and requests over private networking (`*.railway.internal`) skip the edge; the web service is only reached from the public edge (the API and worker never call it). **Checked on the deployed dev service after each change to this code and after Railway networking changes** (runbook `railway.md`, "Rate limit check").
- **Accounts are keyed by HMAC-SHA256** of the trimmed, lower-cased address, so the limiter holds no email (PII) and logs nothing. The HMAC key is **random per process** (`randomBytes(32)` at startup), not an environment variable: the counters live in the same process, so nothing has to agree on the key across restarts or services, and there is no secret to configure, rotate or leak. If the store becomes shared (below), the key becomes a Railway secret at the same time.
- **Store: in memory, per process.** The web service runs one replica (`.railway/railway.ts`). A deploy or restart resets the counters, which gives an attacker at most a fresh window per deploy. **Before a second web replica**, move the store to something shared (Postgres or Redis) behind the same `SlidingWindowLimiter` methods. Memory is bounded: 50 000 keys per limiter (least recently hit evicted first), at most `limit` timestamps each.
- **No `Sb-Forwarded-For`** (decided by the owner, 2026-11-12). Supabase Auth can take a forwarded client IP from a server, but only on requests made with the **secret** key. That would put the secret key (which bypasses RLS and can manage every user) into the web service, which today holds only the publishable key and refuses anything else (ADR 0007). Our own limits cover the gap; Auth's shared per-IP limit stays as a coarse outer bound.

## Consequences
- **Anyone who knows a user's email can lock that account for 15 minutes** with 5 wrong passwords, and keep it locked by repeating that (from enough IPs to stay under the per-IP limit). **Accepted for the pilot** (owner, 2026-11-12): pilot users are few and known, and the alternative (no per-account limit) leaves password guessing against one account unlimited apart from the per-IP limit. The message doesn't say whether the account or the IP is limited. Revisit with MFA enforcement (P3) or if lockouts are reported: e.g. a lock that only applies to IPs the account has never signed in from, or CAPTCHA after failures.
- A user behind the same IP as an attacker (office NAT, mobile carrier) shares the per-IP bucket: 10 attempts / 5 min is generous for one person, tight for a whole office under attack.
- Auth's own per-IP limit still sees only the web server's IP, so a large distributed attack can still exhaust it for everyone (30 sign-ins / 5 min). Our per-IP limit makes that require many IPs; it remains a known limit until Auth can see client IPs without the secret key.
- E2E sends a random `X-Real-IP` per test (`e2e/support/test.ts`), the same header the edge sets in the cloud, so the suite doesn't trip its own limits; one e2e test covers the account lock.
