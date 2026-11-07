# apps/web

Next.js (App Router) + TypeScript strict + Tailwind v4 + shadcn/ui. Node 24, pnpm 12.

## Setup (PowerShell)

```powershell
cd apps/web
pnpm install
# Public env: copy the NEXT_PUBLIC_* lines from the repo-root .env.example into apps/web/.env.local
Set-Content -Encoding utf8 .env.local "NEXT_PUBLIC_API_URL=http://localhost:8000"
pnpm dev
```

## Commands

| Task | Command |
|---|---|
| Dev server | `pnpm dev` |
| Lint / types / unit tests | `pnpm lint` · `pnpm typecheck` · `pnpm test` |
| Production build | `pnpm build` (needs `NEXT_PUBLIC_API_URL`) |
| E2E (first time: `pnpm exec playwright install chromium`) | `pnpm test:e2e` |

If the Playwright browser download fails because of a non-ASCII user profile path:
`$env:PLAYWRIGHT_BROWSERS_PATH="C:\dev\.cache\ms-playwright"` before installing and running.

## Structure

- `src/app/tokens.css`: design tokens (placeholder values until M3). Components use tokens only.
- `src/app/globals.css`: maps tokens into Tailwind; no values here.
- `src/components/ui/`: shadcn/ui primitives (`pnpm dlx shadcn@4.23.0 add <name>`).
- `src/components/states/`: loading / empty / error states; every screen uses them.
- `src/lib/api/client.ts`: typed API client (types generated from OpenAPI in 0.4b).
- `src/lib/env.ts`: Zod-validated public env; the build fails if it is invalid.
