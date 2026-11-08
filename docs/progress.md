# Progress

## Current milestone
M0 — Foundations (plan: `docs/plans/m0.md`)

## Done
- Spec v0.1, CLAUDE.md, Alpha team subagents, CI skeleton
- 0.1 apps/api skeleton (FastAPI /health, settings, JSON logging, worker stub) + fixes for the 6 medium `sec` findings — [PR #1](https://github.com/shalevAIdeveloper/po-agent/pull/1), squash-merged to `dev` as `6465a43`; CI green on `dev` (incl. push-path `paths-filter` with `persist-credentials: false`)
- 0.2 apps/web skeleton (Next 16.4, TS strict, Tailwind v4 placeholder tokens, shadcn/ui, Vitest, Playwright smoke, CI web job on Node 24) + `qa`/`ux-ui` fixes, ADR 0002 — [PR #2](https://github.com/shalevAIdeveloper/po-agent/pull/2), squash-merged to `dev` as `cff4893`; CI green on `dev`
- 0.3 Supabase local + orgs/memberships/RLS (migrations with forced RLS and explicit grants, `create_organization` with 5-per-creator cap + reserved slugs + READ COMMITTED, last-owner trigger, 73 pgTAP tests incl. schema-wide guards, 8 mutations caught, CI `db` job on CLI 2.117.0), ADR 0004 + 0005; `sec` (2 rounds) + `qa` findings fixed — [PR #3](https://github.com/shalevAIdeveloper/po-agent/pull/3), squash-merged to `dev` as `49661ce`; CI green on `dev` (incl. `db` job)
- 0.4a+b API auth (JWKS verification ES256/RS256 only, `po_api` role + per-request RLS with claims allow-list and timeouts, `OrgContext`/`require_role`, `/me`, `/orgs`, `/orgs/current`, 503 `busy`, CORS, OpenAPI → TS types; logging L2/L3/L9/L10), ADR 0003 + 0006; `sec` (3 rounds + delta) + `qa` findings fixed; 180 unit + 10 integration (two real GoTrue users) + 82 pgTAP — [PR #4](https://github.com/shalevAIdeveloper/po-agent/pull/4), squash-merged to `dev` as `674aef9`; CI green on `dev`

## In progress
- 0.4c-1 web login on `feature/auth-web-login` (not yet committed / no PR): `@supabase/ssr` + `proxy.ts` session refresh, `/login`, protected `/app` (orgs from `GET /me`), logout, `requireUser()` with `connection()`, `safeNextPath`, typed API errors; local auth invite-only (D5); API A5/A8/A9 + JWKS first-fetch race fix; e2e against real Supabase + API (CI job `e2e-auth`); ADR 0007. Reviews: `sec` (0 critical/high, 1 medium → A13 pilot gate with 0.5c, 5 low fixed/recorded; delta review: 3 low fixed), `qa` (open redirect via dot segments in `safeNextPath` fixed; 401 redirect loop fixed), `ux-ui` (3 must-fix + 8 should-fix a11y/states fixed; M3 items → W13). Local: api 193 unit + 10 integration, pgTAP 82, web 128 unit, e2e 9/9 ×2.
- 0.4c-2 next (separate PR): invite → `/auth/confirm` → set password, onboarding (create org), password reset

## Next
- 0.5 cloud envs

## Follow-ups
Low-severity `sec` findings from PR #1, each assigned to the task that should close it.

| # | Finding | Fix | Task |
|---|---|---|---|
| ~~L1~~ | ✅ closed in 0.3a — `.gitignore` misses secret-file patterns | Add `*.pem`, `*.key`, `*.p12`, `*.jwk`, `supabase/signing_keys.json`, `*.log`, `apps/api/evals/fixtures/private/` | 0.3a |
| ~~L2~~ | ✅ closed in 0.4a (key fragments, `key`/`*_key` names, nested values, PII keys; token counters kept visible) — Log redaction matches only exact key names, not nested dicts | Match key fragments (`token`, `secret`, `key`, `jwt`, `cookie`, `dsn`…), recurse into dicts/lists; add PII keys (`email`, `address`, `phone`, `raw_text`, `price`) before M1 | 0.4a |
| ~~L3~~ | ✅ closed in 0.4a — `LOG_LEVEL` not validated (bad value → `KeyError` at startup) | `Literal["DEBUG","INFO","WARNING","ERROR"]` in `Settings` | 0.4a |
| L4 | `.env` paths resolve from the CWD; a `.env` baked into an image would load in prod | Paths from `Path(__file__)`; skip `.env` files when `APP_ENV != local`; `.dockerignore` with `.env*` | 0.5c |
| L5 | `/docs` and `/openapi.json` public on deployed dev | Enable only for `APP_ENV=local` (or protect in dev) | 0.5c |
| L6 | `fastapi[standard-no-fastapi-cloud-cli]` pulls OpenTelemetry SDK + OTLP exporter, can be switched on by env alone | Depend on `fastapi` + `uvicorn[standard]` explicitly, or set `OTEL_SDK_DISABLED=true` in deployed envs | 0.5c |
| L7 | Callers choose their own `x-request-id` (can mix their requests into a victim's log trail) | Trust incoming ids only behind Railway's proxy, or log server id + client id separately | 0.5c |
| L8 | `/health` exposes env and version unauthenticated | Return only `status` unless uptime checks need more | 0.5c |
| ~~L9~~ | ✅ closed in 0.4a — stdlib records are interpolated before processors run, so PII in `%s` args (asyncpg, uvicorn "Invalid HTTP request", `logger.error("failed: %s", exc)`) reaches logs | Pre-chain processor: outside an allow-list (`uvicorn.error`, `app.*`), log the template `record.msg` instead of the interpolated message; test with a PII arg | 0.4a |
| ~~L10~~ | ✅ closed in 0.4a — `log.error("x", exception=str(e))` bypasses sanitization; `stack_info=True` is not rendered; frame paths are absolute (show the user's home dir) | Drop/redact the `exception` key in `sanitize_exception`; add `StackInfoRenderer` or drop `stack_info`; make frame paths relative to `app/` | 0.4a |
| L11 | DB driver messages (asyncpg/Postgres) can quote values even with `hide_parameters=True` | Never return DB error text in HTTP responses; Sentry `before_send` must strip `exception.values[*].value` | M7 (Sentry) |

From the 0.2 (`apps/web`) `qa` and `ux-ui` reviews:

| # | Finding | Fix | Task |
|---|---|---|---|
| W1 | `states.test.tsx` gaps: `LoadingState` default line count, omitted `description`/`action` | Add cases when state components are reworked | M3 |
| W2 | E2E in CI only ever sees the "API unreachable" state | Run the API alongside web e2e in CI and assert "API is up" | 0.5a |
| ~~W3~~ | ✅ 0.4c-1 (README note + stop command, pending merge) — `reuseExistingServer` locally can silently reuse a stale `next start` on port 3100 | Note in `apps/web/README.md` (kill leftovers / use a different port) | 0.4c |
| ~~W4~~ | ✅ 0.4c-1 (pending merge) — Client timeout (5s) equals Playwright's default `expect` timeout; a hung local API makes the smoke test borderline | `toBeVisible({ timeout: 10_000 })` in `e2e/smoke.spec.ts` | 0.4c |
| W5 | `retries: 1` in CI can hide flakiness; Playwright browsers not cached | Watch the `github` reporter's flaky list; cache `~/.cache/ms-playwright` | 0.5a |
| W6 | Token categories missing: type scale (pick names that `cn` won't confuse with colors, + unit test), density/row & control height, focus-ring width/offset (current `ring-ring/50` likely < 3:1), status `-foreground`/`-subtle` pairs, z-index layers, `--elevation-lg`, a `.num` utility (mono + tabular-nums) | Add to `tokens.css` / `globals.css` | M3 |
| W7 | Decide whether `--destructive` (actions) and `--status-danger` (flags) stay separate (same value today) | Decide in the M3 token pass | M3 |
| W8 | Hard-coded values in generated `button.tsx` (`text-[0.8rem]`, `rounded-[min(...,10px)]`, `color-mix(...)`, `translate-y-px`); `button.tsx` imports `cn` directly instead of `@/lib/utils` | Restyle with tokens; lint rule against arbitrary px/rem/oklch values and direct `cn` imports | M3 |
| W9 | State components: `LoadingState` table variant (rows/cols matching row height), icon slot + size on Empty/Error, heading level for titles, `ErrorState` politeness (`assertive`/`polite`), shared frame for Empty/Error | Extend when inbox/review screens are built | M3 |
| W10 | `<main>` repeated in every page; no skip link / nav landmark / `min-h-dvh` | App shell in layout: `<main id="main">`, skip link, `<nav>` | M3 |
| W11 | Keyboard-first layer is not provided by shadcn/Base UI: J/K row nav, scoped shortcuts, `?` sheet, command palette (verify `cmdk` + Base UI Dialog focus), Base UI `render` prop vs Radix `asChild` | ADR + shortcut layer | M3 |
| W12 | Theme toggle + pre-paint script (ADR 0002); the inline script needs a CSP nonce/hash later | Implement with the toggle | M3 |

From 0.3 (database):

| # | Item | Fix | Task |
|---|---|---|---|
| D1 | Supabase CLI pinned at 2.117.0 (local + CI); v3.0 is local-by-default and removes deprecated commands | Separate `chore:` PR: upgrade local + CI pin together, re-run `db`/`test db`/advisors | after M0 |
| D2 | Transfer ownership before leaving: the last-owner trigger blocks deleting a user who is the sole owner of an org | API/UI flow to transfer ownership (and to leave/delete an org) | F7 |
| D3 | RLS policies call a membership function per row; fine for orgs/memberships, slow for large tenant tables | Use a set-returning helper (`org_id in (select private.user_org_ids())`) for orders/lines | M1/M2 |
| ~~D4~~ | ✅ errors mapped in 0.4b (`slug_unavailable`, `organization_limit_reached`, `invalid_organization`, never raw DB text); rate limit moved to A1 — Duplicate-slug error (`23505`) tells a caller that another tenant owns that slug (`sec` L3) | API maps `23505` → generic "slug unavailable" and `53400` → "organization limit reached"; rate-limit `POST /orgs`; never return raw DB error text | A1 (M7) |
| ~~D5~~ | ✅ 0.4c-1 (pending merge: `[auth] enable_signup = false`, confirmations on, min 12, `secure_password_change`; `[auth.email] enable_signup` stays true — it gates the whole email provider) — Local auth allows open sign-up without email confirmation (`config.toml`: `enable_signup = true`, `enable_confirmations = false`), while the pilot is invite-only (`sec` M2) | Local: `enable_signup = false` in `[auth]` and `[auth.email]`, confirmations on; e2e creates users via the admin API / Mailpit | 0.4c |
| D7 | A user who is the sole owner of an org cannot delete their account (blocked with `23514`, pinned by pgTAP); this matters for account/data deletion requests (`sec` re-review L-A) | Runbook: "transfer ownership or delete the org before deleting the user"; build the flow with D2 | 0.5c (runbook) / F7 |
| D8 | The last-owner trigger locks the org with a no-op `UPDATE`, which fires real UPDATE side effects on `organizations` (future `updated_at`/audit triggers, Realtime) (`sec` re-review L-B) | Any UPDATE trigger on `organizations` must ignore `old is not distinct from new`; add a guard test when the first one is added | M5 (audit) |
| ~~D9~~ | ✅ closed in 0.4b (retry on 40001/40P01 in `run_as_user`; lock-org-first rule in ADR 0003) — Deadlock (`40P01`) possible if a transaction updates an org and then removes an owner (locks taken in the opposite order to the trigger) (`sec` re-review L-C) | Service code locks the org first (`for no key update`), then changes memberships; treat `40P01`/`40001` as retryable | 0.4b |
| D10 | Reserved slugs are a hard-coded CHECK; list incomplete (`bounce(s)`, `inbound`, `notifications`, `unsubscribe`, `contact`, `privacy`, `legal`, `auth`, `login`, `webhook(s)`, `autoconfig`, `autodiscover`, `smtp`, `imap`, `dmarc`, `po-agent`…); adding a word fails if an org already uses it (`sec` re-review L-D) | Move to `private.reserved_slugs` checked in `create_organization` (+ service_role insert trigger); existing slugs are grandfathered | M6 |
| D11 | Advisory lock keys share one global space with any future locks, e.g. a worker queue (`sec` re-review L-E) | Document a key-prefix convention (`'<function>:' \|\| id`) in ADR 0004 when the second advisory lock appears | M1 |
| D12 | The creation cap counts orgs that currently exist with `created_by` = user; deleting an org frees a slot | Consider a cumulative creation quota (option A: `private.org_creation_quota` counter with conditional upsert) if we see create/delete loops being abused | when observed (M7) |
| D6 | Hosted auth hardening (`sec` M2 + L5): sign-up off (invite-only), email confirmations on, password ≥ 12 with no composition rules (NIST 800-63B, decided 2026-11-08), `secure_password_change`, TOTP MFA for owners/admins, custom SMTP; mirror the same values in local `config.toml` | Configure `po-agent-dev` / `po-agent-prod` | 0.5b |

From 0.4 (auth):

| # | Item | Fix | Task |
|---|---|---|---|
| A1 | Rate limiting for `POST /orgs` and auth-adjacent endpoints (moved from D4) | Per-user + per-IP limits at the API (and Supabase Auth rate limits) | M7 |
| A2 | Hosted `po_api` (`sec` M2/M4, `qa` m7): password set out of band; Supavisor user `po_api.<project-ref>`; `grant ... with inherit false, set true` needs PG ≥ 16 and `postgres` holding ADMIN on `authenticated`; never deploy with `db push --include-roles` (would push the local password from `supabase/roles.sql`) | Runbook: set the password in `po-agent-dev`/`-prod`, verify the local password is rejected there, store `DATABASE_URL` in Railway only; use a direct/session-mode connection, or transaction mode with asyncpg `statement_cache_size=0` + `prepared_statement_cache_size=0`; keep transaction-local `set_config` (never session-level `SET ROLE`); JWKS key rotation: publish the standby key at least one TTL (10 min) before rotating, or new tokens get 401 for up to 30 s | 0.5b |
| A3 | Tokens of a deleted/demoted user stay valid until expiry (1 h); membership is re-checked per request, but the session lingers (`sec` L1) | Shorter `jwt_expiry` and/or `session_id` checks against `auth.sessions` for sensitive operations (approve/export) | M7 |
| A4 | `po_api` can create temp tables (database `TEMP` via `PUBLIC`) (`sec` L5) | Harmless today (all SQL is schema-qualified); decide `revoke temporary on database postgres from public` for hosted, or document | 0.5b |
| ~~A5~~ | ✅ 0.4c-1 (pending merge: `ErrorResponse` on 400/401/403/404/409/503; 422 left as generated) — Error responses (401/403/404/409/503 + `detail` codes) are not in the OpenAPI schema, so the TS client has no error types (`qa` m8) | `responses=` on routes; regenerate `schema.d.ts` | 0.4c |
| A6 | Local integration runs can hit GoTrue's sign-in rate limit (30 / 5 min / IP) and fail after laptop sleep if Docker's clock drifts > 30 s (`qa` m4) | Runbook note (wait / `supabase stop && supabase start`) | 0.5c |
| A7 | JWKS refresh blocks requests: when the 10-min TTL expires, every request waits for the refetch (bounded by a 5 s total timeout) (`sec` re-review L-b) | Serve still-valid cached keys while one refresh runs; only unknown-`kid` callers wait | M7 |
| ~~A8~~ | ✅ 0.4c-1 (pending merge) — Small items from `sec` delta review: log `db.statement_timeout` vs `db.lock_timeout` separately (a slow query should alert, not hide behind 503 `busy`); `expose_headers` must include `Retry-After` before the web reads it; port check in `config.py` relies on `urlsplit` raising (dead branch) and accepts `:0` | Fix with the web client's error handling | 0.4c |
| ~~A9~~ | ✅ 0.4c-1 (pending merge: `timeout = 60`, `thread` method) — No global test timeout: a hung test (e.g. a frozen event-loop clock) blocks until the CI job times out | Add `pytest-timeout` (e.g. `timeout = 60` in pytest config, longer for integration) | 0.4c |
| A11 | Supabase changelog 2026-06-03: new Free-plan projects on the default SMTP cannot customize auth email templates; 0.4c-2 needs `token_hash` links to `/auth/confirm` in the invite/recovery templates | Custom SMTP on `po-agent-dev`/`-prod` (already in D6) before relying on custom templates; verify in 0.5b | 0.5b |
| A12 | Found in 0.4c-1: concurrent requests during the first JWKS fetch got 503 (they skipped the lock); fixed — callers missing their `kid` now wait for an in-flight refresh | Fixed with tests; A7 (serve cached keys during a TTL refresh) stays open | ✅ 0.4c-1 |
| A13 | 🚧 **Mandatory gate before pilot.** `sec` M (0.4c-1): sign-in runs in a Server Action, so Supabase Auth's per-IP limit (30 / 5 min) sees the web server's IP: one shared bucket for all users (anyone can lock everyone out), and no per-account limit | Decided 2026-11-08: separate PR with 0.5c — limiter in the login action keyed by client IP (trusted Railway proxy header) + email hash, returning the existing 429 message; ADR 0007 update | 0.5c (pilot blocker) |
| A14 | Hosted Auth settings are not controlled by `config.toml`: signup off, confirmations on, min 12, `secure_password_change` must be set in both projects or invite-only exists only locally (`sec` L) | Checklist in the 0.5b runbook (or `supabase config push`), verified before release; with D6 | 0.5b |
| A15 | Test gaps (`qa`, 0.4c-1): `login`/`logout`/`requireUser` only covered by e2e; removing `await connection()` is caught only by lint (unused import); no e2e for the 429 message or token refresh | Add Server Action unit tests with a mocked Supabase client when the auth flows grow (0.4c-2) | 0.4c-2 |
| W13 | a11y for M3 (`ux-ui`, 0.4c-1): `border-input` field boundary ~1.2:1 (< 3:1); move focus to the main heading after route changes; `button.tsx` xs/sm hard-coded sizes (W8) | M3 token / app-shell pass | M3 |
| A10 | Leaked password protection (HaveIBeenPwned check, NIST 800-63B blocklist) — not available locally, may need a paid plan | Enable in `po-agent-dev`/`-prod` if the plan allows; otherwise record that it is not available | 0.5b |

## Blockers / open questions
- **Pilot gate:** A13 (login rate limit per client IP + email) must be merged before any pilot user gets access — planned with 0.5c
- Which ERP to integrate first → decided by customer discovery (target: 20 calls)
- ~~How pilot users get accounts~~ — decided 2026-11-08: manual invite from the Supabase dashboard (runbook in `docs/runbooks/`); in-app invitations screen in F7

## Eval baseline
| Date | Prompt | Model | Header acc. | Line acc. |
|---|---|---|---|---|
| — | — | — | — | — |
