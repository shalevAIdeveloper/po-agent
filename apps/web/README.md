# apps/web

Next.js (App Router) + TypeScript strict + Tailwind v4 + shadcn/ui. Node 24, pnpm 12.

## Setup (PowerShell)

```powershell
cd apps/web
pnpm install
# Public env only (NEXT_PUBLIC_*, see the repo-root .env.example). Create .env.local in an
# editor, or (PS 5.1 `>` writes UTF-16, so use Set-Content -Encoding utf8):
Set-Content -Encoding utf8 .env.local @(
  "NEXT_PUBLIC_API_URL=http://localhost:8000",
  "NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321",
  "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<PUBLISHABLE_KEY from ``supabase status``>"
)
pnpm dev
```

Sign-up is disabled (invite-only). For a local user, create one in Studio
(http://127.0.0.1:54323 → Authentication → Add user, "Auto confirm"), with a password of
at least 12 characters.

## Commands

| Task | Command |
|---|---|
| Dev server | `pnpm dev` |
| Lint / types / unit tests | `pnpm lint` · `pnpm typecheck` · `pnpm test` |
| Production build | `pnpm build` (needs the three `NEXT_PUBLIC_*` vars) |
| E2E smoke (first time: `pnpm exec playwright install chromium`) | `pnpm test:e2e` |
| E2E incl. auth (real Supabase + API) | see below |

### E2E with auth

Needs `supabase start` (from the repo root). Playwright starts the API (`uv run uvicorn`
as `po_api`) and the web app itself; the secret key is used only by the test helpers to
create and delete test users.

```powershell
$env:E2E_AUTH = "1"
$env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "<PUBLISHABLE_KEY from supabase status>"
$env:E2E_SUPABASE_SECRET_KEY = "<SECRET_KEY from supabase status>"
pnpm test:e2e
```

Without `E2E_AUTH` the auth specs are skipped (that is what the `web` CI job runs; the
`e2e-auth` job runs them).

**Stale servers (W3):** locally Playwright reuses anything already listening on ports
3100 (web) and 8000 (API), even an old build or an API with other settings. If results
look stale, stop leftovers first:
`Get-NetTCPConnection -LocalPort 3100,8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess }`.

If the Playwright browser download fails because of a non-ASCII user profile path:
`$env:PLAYWRIGHT_BROWSERS_PATH="C:\dev\.cache\ms-playwright"` before installing and running.

## Structure

- `src/app/tokens.css`: design tokens (placeholder values until M3). Components use tokens only.
- `src/app/globals.css`: maps tokens into Tailwind; no values here.
- `src/components/ui/`: shadcn/ui primitives (`pnpm dlx shadcn@4.23.0 add <name>`).
- `src/components/states/`: loading / empty / error states; every screen uses them.
- `src/lib/api/client.ts`: typed API client (types generated from OpenAPI, ADR 0006); `errors.ts` maps failures to UI text.
- `src/lib/env.ts`: Zod-validated public env; the build fails if it is invalid.
- `src/proxy.ts` + `src/lib/supabase/`: session refresh and route guard (ADR 0007).
- `src/lib/auth/`: `requireUser()` (the check every protected page/action runs), `next` sanitizing, login validation.
