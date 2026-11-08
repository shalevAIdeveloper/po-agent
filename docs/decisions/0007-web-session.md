# 0007 — Web session: cookies, proxy refresh, request-time verification

## Context
0.4c-1 adds sign-in to `apps/web` (Next.js 16, Cache Components on). Supabase sessions live in cookies managed by `@supabase/ssr`; the API verifies the access token itself (ADR 0003). Server Components cannot write cookies, and Next.js 16 renamed `middleware.ts` to `proxy.ts`.

## Decision
- **Clients:** `@supabase/ssr` with the publishable key only (`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`; the env schema rejects an `sb_secret_` value). One server client per request (`lib/supabase/server.ts`); no browser client yet.
- **Refresh in `src/proxy.ts`:** on every non-static request, `getClaims()` refreshes an expiring token and verifies the JWT signature. Refreshed cookies go to both the request (for Server Components) and the response, with the library's no-cache headers; redirects copy them over.
- **The proxy is not the boundary:** it only redirects guests from `/app/**` to `/login?next=…` and signed-in users away from `/login` (GET/HEAD only, so a Server Action POST is never replayed by a 307). The matcher always includes `/app/:path*`, even for paths with a file extension. Every protected page and Server Action calls `requireUser()`, and the API verifies the token on every call.
- **`requireUser()`:** `getSession()` supplies the raw access token (and refreshes it once if needed; nothing from it is trusted), then `getClaims(token)` verifies that exact JWT. One refresh path per render, so no refresh-token reuse race; a refresh in a Server Component cannot persist cookies, which is fine because the proxy refreshed first.
- **API says 401 but the token verified locally** (revoked, or web/API config drift): `/app` shows "Your session has ended" with a "Sign in again" (sign-out) button instead of redirecting, which would loop between `/login` and `/app`.
- **Request time only:** `requireUser()` starts with `await connection()`, so session-bound rendering never happens during prerender or Partial Prefetching (token verification reads the clock, and session data must not be cached in a prefetch). The access token never reaches a Client Component.
- **`next` redirects:** only same-origin paths (`safeNextPath`), checked when rendering the hidden field and again in the action; the result never starts with `//` (dot segments such as `/.//evil.com` normalize to that), and the function is idempotent.
- **Env:** the public key must match `sb_publishable_…`, which also rejects secret and legacy JWT keys.
- **Login errors:** one message for wrong password / unknown email / unconfirmed / banned; separate messages only for rate limiting and Auth being down. Nothing about attempts is logged (the email is PII).
- **Email links (0.4c-2):** invite and recovery templates link to `/auth/confirm?token_hash=…&type=…` (Site URL). GET only renders a "Continue" button; `verifyOtp` runs in a Server Action on POST, so email security scanners that prefetch links (e.g. Safe Links) can't use up the one-time token. Only `invite` and `recovery` are accepted; anything else never reaches Auth. The page sets `<meta name="referrer" content="no-referrer">` (the URL carries the token). Links expire after 1 h (`otp_expiry`, shared by both; decided 2026-11-08 and kept against `sec`'s 15-min recommendation: reps don't always open email right away, and GET-safe links + 8-digit codes + single use keep the risk low; Follow-up A21) — expired invites are re-sent (runbook).
- **Passwords:** set at `/set-password`, only within 15 minutes of an invite/recovery link sign-in (JWT `amr` `otp` entry; a stolen or unattended session cannot replace the password) and showing the account's email; `/auth/confirm` signs out a different signed-in user before verifying (no silent session swap). ≥ 12 characters (code points; Auth's own minimum counts bytes, A19), ≤ 72 bytes (bcrypt), confirmation must match; Auth's `weak_password` / `same_password` / reauthentication errors are mapped. Reset requests always answer "If an account exists…".
- **Onboarding:** only users with no organization see `/onboarding` (`/app` redirects them; `/onboarding` redirects members to `/app`). Users added to an existing org (operator script until F7, A16) skip it.
- **Sign-out:** `signOut({ scope: "local" })` — ends this browser's session; "sign out everywhere" is a later feature.
- **Active org:** not stored yet; `/app` lists the user's orgs from `GET /me`. The `X-Org-Id` cookie + switcher arrive with onboarding (0.4c-2) / M3.

- **Open:** sign-in calls Auth from the web server, so Auth's per-IP limit (30 / 5 min) is one bucket shared by all users (`sec` M, 0.4c-1). See Follow-up A13: decided to fix in a separate PR with 0.5c (limiter in the login action per client IP + email hash); a mandatory gate before the pilot.

## Consequences
- Protected pages are dynamic per request and are not prefetched; acceptable for an authenticated workspace.
- Invite-only locally too: `[auth] enable_signup = false`. `[auth.email] enable_signup` must stay `true` — in the CLI it switches the whole email provider, password login included.
- E2E runs against the real stack (`E2E_AUTH=1`, CI job `e2e-auth`); test users are created and deleted through the admin API.
