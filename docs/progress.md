# Progress

## Current milestone
M0 — Foundations (plan: `docs/plans/m0.md`)

## Done
- Spec v0.1, CLAUDE.md, Alpha team subagents, CI skeleton
- 0.1 apps/api skeleton (FastAPI /health, settings, JSON logging, worker stub) + fixes for the 6 medium `sec` findings — [PR #1](https://github.com/shalevAIdeveloper/po-agent/pull/1), squash-merged to `dev` as `6465a43`; CI green on `dev` (incl. push-path `paths-filter` with `persist-credentials: false`)

## In progress
- 0.2 apps/web skeleton (Next 16.4, TS strict, Tailwind v4 tokens placeholder, shadcn/ui, Vitest, Playwright smoke, CI web job on Node 24) — PR to `dev` open, not merged. `qa` major + 5 `ux-ui` fixes applied; ADR 0002 (theme class on `<html>`).

## Next
- 0.3 Supabase local + orgs/memberships/RLS → 0.4 auth → 0.5 cloud envs

## Follow-ups
Low-severity `sec` findings from PR #1, each assigned to the task that should close it.

| # | Finding | Fix | Task |
|---|---|---|---|
| L1 | `.gitignore` misses secret-file patterns | Add `*.pem`, `*.key`, `*.p12`, `*.jwk`, `supabase/signing_keys.json`, `*.log`, `apps/api/evals/fixtures/private/` | 0.3a |
| L2 | Log redaction matches only exact key names, not nested dicts | Match key fragments (`token`, `secret`, `key`, `jwt`, `cookie`, `dsn`…), recurse into dicts/lists; add PII keys (`email`, `address`, `phone`, `raw_text`, `price`) before M1 | 0.4a |
| L3 | `LOG_LEVEL` not validated (bad value → `KeyError` at startup) | `Literal["DEBUG","INFO","WARNING","ERROR"]` in `Settings` | 0.4a |
| L4 | `.env` paths resolve from the CWD; a `.env` baked into an image would load in prod | Paths from `Path(__file__)`; skip `.env` files when `APP_ENV != local`; `.dockerignore` with `.env*` | 0.5c |
| L5 | `/docs` and `/openapi.json` public on deployed dev | Enable only for `APP_ENV=local` (or protect in dev) | 0.5c |
| L6 | `fastapi[standard-no-fastapi-cloud-cli]` pulls OpenTelemetry SDK + OTLP exporter, can be switched on by env alone | Depend on `fastapi` + `uvicorn[standard]` explicitly, or set `OTEL_SDK_DISABLED=true` in deployed envs | 0.5c |
| L7 | Callers choose their own `x-request-id` (can mix their requests into a victim's log trail) | Trust incoming ids only behind Railway's proxy, or log server id + client id separately | 0.5c |
| L8 | `/health` exposes env and version unauthenticated | Return only `status` unless uptime checks need more | 0.5c |
| L9 | stdlib records are interpolated before processors run, so PII in `%s` args (asyncpg, uvicorn "Invalid HTTP request", `logger.error("failed: %s", exc)`) reaches logs | Pre-chain processor: outside an allow-list (`uvicorn.error`, `app.*`), log the template `record.msg` instead of the interpolated message; test with a PII arg | 0.4a |
| L10 | `log.error("x", exception=str(e))` bypasses sanitization; `stack_info=True` is not rendered; frame paths are absolute (show the user's home dir) | Drop/redact the `exception` key in `sanitize_exception`; add `StackInfoRenderer` or drop `stack_info`; make frame paths relative to `app/` | 0.4a |
| L11 | DB driver messages (asyncpg/Postgres) can quote values even with `hide_parameters=True` | Never return DB error text in HTTP responses; Sentry `before_send` must strip `exception.values[*].value` | M7 (Sentry) |

From the 0.2 (`apps/web`) `qa` and `ux-ui` reviews:

| # | Finding | Fix | Task |
|---|---|---|---|
| W1 | `states.test.tsx` gaps: `LoadingState` default line count, omitted `description`/`action` | Add cases when state components are reworked | M3 |
| W2 | E2E in CI only ever sees the "API unreachable" state | Run the API alongside web e2e in CI and assert "API is up" | 0.5a |
| W3 | `reuseExistingServer` locally can silently reuse a stale `next start` on port 3100 | Note in `apps/web/README.md` (kill leftovers / use a different port) | 0.4c |
| W4 | Client timeout (5s) equals Playwright's default `expect` timeout; a hung local API makes the smoke test borderline | `toBeVisible({ timeout: 10_000 })` in `e2e/smoke.spec.ts` | 0.4c |
| W5 | `retries: 1` in CI can hide flakiness; Playwright browsers not cached | Watch the `github` reporter's flaky list; cache `~/.cache/ms-playwright` | 0.5a |
| W6 | Token categories missing: type scale (pick names that `cn` won't confuse with colors, + unit test), density/row & control height, focus-ring width/offset (current `ring-ring/50` likely < 3:1), status `-foreground`/`-subtle` pairs, z-index layers, `--elevation-lg`, a `.num` utility (mono + tabular-nums) | Add to `tokens.css` / `globals.css` | M3 |
| W7 | Decide whether `--destructive` (actions) and `--status-danger` (flags) stay separate (same value today) | Decide in the M3 token pass | M3 |
| W8 | Hard-coded values in generated `button.tsx` (`text-[0.8rem]`, `rounded-[min(...,10px)]`, `color-mix(...)`, `translate-y-px`); `button.tsx` imports `cn` directly instead of `@/lib/utils` | Restyle with tokens; lint rule against arbitrary px/rem/oklch values and direct `cn` imports | M3 |
| W9 | State components: `LoadingState` table variant (rows/cols matching row height), icon slot + size on Empty/Error, heading level for titles, `ErrorState` politeness (`assertive`/`polite`), shared frame for Empty/Error | Extend when inbox/review screens are built | M3 |
| W10 | `<main>` repeated in every page; no skip link / nav landmark / `min-h-dvh` | App shell in layout: `<main id="main">`, skip link, `<nav>` | M3 |
| W11 | Keyboard-first layer is not provided by shadcn/Base UI: J/K row nav, scoped shortcuts, `?` sheet, command palette (verify `cmdk` + Base UI Dialog focus), Base UI `render` prop vs Radix `asChild` | ADR + shortcut layer | M3 |
| W12 | Theme toggle + pre-paint script (ADR 0002); the inline script needs a CSP nonce/hash later | Implement with the toggle | M3 |

## Blockers / open questions
- Which ERP to integrate first → decided by customer discovery (target: 20 calls)
- Invite-only sign-up (decided) vs. invitations deferred to F7: decide before 0.4c how pilot users get accounts

## Eval baseline
| Date | Prompt | Model | Header acc. | Line acc. |
|---|---|---|---|---|
| — | — | — | — | — |
