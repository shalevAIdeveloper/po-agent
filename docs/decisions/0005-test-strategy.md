# 0005 — Test strategy

## Context
Tenant isolation is the highest-risk property of the system and lives in two layers (database RLS and API scoping). Each layer needs its own proof, and the proofs must run in CI on every change that could affect them.

## Decision
| Layer | Tool | Where | Proves |
|---|---|---|---|
| Database (RLS, grants, functions, triggers) | pgTAP via `supabase test db` | `supabase/tests/*.test.sql` | Isolation as `authenticated` with real JWT claims (`set local role` + `request.jwt.claims`), privileges of `anon`, schema-wide guards |
| API (services, logging, DB access) | pytest (+ `-m integration` against local Postgres) | `apps/api/tests` | Config, logging without PII, DB error handling today; org scoping in the API from 0.4b |
| Web units | Vitest + Testing Library | `apps/web/src/**/*.test.ts(x)` | Client, env validation, UI states |
| Critical flows | Playwright | `apps/web/e2e` | Smoke today; login → upload → review → approve → export as milestones land |

- pgTAP files run each in its own transaction (`begin … rollback`), are independent of order, and insert their own synthetic fixtures. **Fixture convention:** ids start with `feed0000-`, slugs with `pgtap-`, emails end in `@pgtap.test`, so fixtures never collide with local dev data or a future `seed.sql`.
- Schema-wide guard tests (`00_security_guards`) assert properties over **all** objects, not a list, so new tables, views and functions are covered without new tests.
- **Mutation check for security-critical tests**: when such a test is written, deliberately break the property against the local DB (`supabase db query --local`), confirm the suite fails, then `supabase db reset`. Mutations run for 0.3 (2026-11-07), each caught:
  1. `memberships` without `FORCE ROW LEVEL SECURITY`
  2. `private.is_org_member` always returns true
  3. `create_organization` without a pinned `search_path`
  4. last-owner trigger dropped
  5. `anon` granted `SELECT` on `organizations`
  6. last-owner trigger made `SECURITY INVOKER` again (the RLS-hidden-org bypass found by `sec`)
  7. `service_role` granted `TRUNCATE` on `memberships`
  8. reserved-slug constraint dropped
- Integration tests skip locally without a database but **fail** when `CI` is set, so a misconfigured job cannot pass by skipping; CI runs them with `-rs`.
- CI `db` job: `supabase db start` → `db lint` → `db advisors` → `test db` → API integration tests, with the CLI pinned to the local version.

## Consequences
- Two independent proofs of isolation (DB and API from 0.4b); a bug in one layer is still caught by the other.
- DB tests need Docker locally and add roughly 2.5–4 min to CI on a cold runner (image pulls for Postgres and pg_prove).
