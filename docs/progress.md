# Progress

## Current milestone
M0 — Foundations (plan: `docs/plans/m0.md`)

## Done
- Spec v0.1, CLAUDE.md, Alpha team subagents, CI skeleton

## In progress
- 0.1 apps/api skeleton — PR #1 (`feature/api-skeleton` → `dev`), incl. fixes for the 6 medium `sec` findings. Not merged yet.

## Next
- 0.2 apps/web skeleton → 0.3 Supabase local + orgs/memberships/RLS → 0.4 auth → 0.5 cloud envs

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

## Blockers / open questions
- Which ERP to integrate first → decided by customer discovery (target: 20 calls)
- Invite-only sign-up (decided) vs. invitations deferred to F7: decide before 0.4c how pilot users get accounts

## Eval baseline
| Date | Prompt | Model | Header acc. | Line acc. |
|---|---|---|---|---|
| — | — | — | — | — |
