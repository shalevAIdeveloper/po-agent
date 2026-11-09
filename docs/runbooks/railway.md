# Runbook — Railway (dev now, production at P1)

Three services in one Railway project `po-agent`, environment `dev`: `web` (Next.js), `api` (FastAPI) and `worker` (same image as the API). Design: ADR 0008. Infrastructure: `.railway/railway.ts`.

Nothing in Railway is created, changed or deployed without the owner approving the exact command or plan first.

## Costs (checked 2026-11-09, railway.com/pricing)
- **Hobby:** $5/month, includes $5 of usage. Usage: RAM ≈ $10 per GB-month, CPU ≈ $20 per vCPU-month, egress $0.05/GB.
- Expected for dev (3 mostly idle services, ~0.4–0.6 GB RAM): **about $5–8/month in total**.
- Owner sets a usage limit in Workspace → Usage (e.g. $10, alert at $7).

## 1. Owner (once, in the browser)
1. Workspace → Billing: **Hobby**.
2. Workspace → Usage: usage limit + alert.
3. GitHub: install the **Railway** GitHub app with access to **`po-agent` only** (needed before autodeploy, step 6).
4. `railway login` in your own terminal; check with `railway whoami`.

## 2. Project and environment (Claude, after approval)
```powershell
railway init --name po-agent           # creates the project; default environment is "production"
# Rename the default environment to "dev" in the dashboard (Project → Settings → Environments),
# or create "dev" and delete the empty "production". production is added at P1.
railway link --project po-agent --environment dev
```

## 3. Services from `.railway/railway.ts` (Claude, after approval)
```powershell
cd .railway; pnpm install --frozen-lockfile
railway config plan                     # read every line; values are redacted
# owner approves this exact plan, then:
railway config apply
```
Then generate the Railway domains (not in source): `railway domain --service api` and `railway domain --service web`. The references `${{api.RAILWAY_PUBLIC_DOMAIN}}` / `${{web.RAILWAY_PUBLIC_DOMAIN}}` in `railway.ts` resolve to them.

## 4. Secrets (owner, in the dashboard — never in chat)
| Service | Variable | Value |
|---|---|---|
| `api` | `DATABASE_URL` | `po_api` via the **session pooler**: `postgresql://po_api.<project-ref>:<password>@<pooler-host>:5432/postgres?sslmode=require` (Supabase → Connect → Session pooler; the user is `po_api.<project-ref>`) |
| `api` | `DATABASE_CA_CERT` | The Supabase CA certificate (Dashboard → Database Settings → SSL Configuration → Download certificate), pasted as text including the `BEGIN/END CERTIFICATE` lines. Public, not a secret; never committed (certificates stay out of the repo and the upload). The API refuses to start without it outside local and connects with TLS verify-full (chain + hostname) |
| `web` | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_…` of the same project (public, but set here so it is never mistaken for the secret key) |

`worker.DATABASE_URL` and `worker.DATABASE_CA_CERT` are references to the `api` variables; nothing to set. Before the first deploy, check the pooler with the same CA: `uv run python -m tools.check_api_role --ca-file <downloaded .crt>` (verify-full). If that fails, stop: the API would fail the same way. `SUPABASE_SECRET_KEY` and LLM keys are not set until a service needs them.

## 5. First deploy by upload, before the merge (Claude, after approval)
Upload from a **clean worktree of the PR's commit**: it contains only tracked files, so a local `.env`, key or `node_modules` cannot be uploaded even if an ignore rule were missed. The `.railwayignore` files (repo root and `apps/*/`) are a second layer.
```powershell
git worktree add ..\po-agent-deploy <PR head commit>     # detached, tracked files only
cd ..\po-agent-deploy
```
Print the exact upload list and check it (must not contain `.env*`, keys/certs, `supabase/roles.sql`, `node_modules`, `tools/`, `tests/`, `evals/`); the owner sees it before the upload:
```bash
# Git Bash, from the worktree root; approximates Railway's filter: .gitignore + .railwayignore
for app in apps/api apps/web; do
  all=$(find "$app" -type f | sort)
  comm -23 <(printf '%s\n' "$all") \
    <(printf '%s\n' "$all" | git -c core.excludesFile=.railwayignore check-ignore --no-index --stdin | sort)
done
```
Then, from the worktree:
```powershell
railway --version                                  # IaC needs ≥ 5.42.1 (0.5c used 5.71.0)
railway up apps/api --service api --environment dev --detach -m "0.5c first deploy"
railway up apps/api --service worker --environment dev --detach -m "0.5c first deploy"
railway up apps/web --service web --environment dev --detach -m "0.5c first deploy"
railway deployment list --service api --json     # follow the deployment id until SUCCESS
cd ..\po-agent; git worktree remove ..\po-agent-deploy
```
A deploy counts only when that deployment reports `SUCCESS` (not when the upload returns).

## 6. Autodeploy from `dev` (after the PR is merged)
Small PR: `GITHUB_AUTODEPLOY = true` in `.railway/railway.ts` → plan → owner approves → apply. From then on a push to `dev` deploys each service whose watch paths changed, after CI passed (`checkSuites`).

## 7. Verify
- `curl https://<api-domain>/health` → `{"status":"ok"}`; `/docs` → 404.
- `https://<web-domain>/healthz` → `{"status":"ok"}`; the home page shows "API is up".
- `railway logs --service worker --lines 50` contains `worker.started`.
- A18 (before any real invite goes through the cloud domain): request `https://<web-domain>/auth/confirm?token_hash=test-not-a-token&type=invite`, then check `railway logs --service web --http` (and the dashboard HTTP logs) for whether the query string is recorded. Record the result in `docs/progress.md` (A18).
- Supabase Auth (A14): `site_url` and redirect URLs point at the web domain (`supabase-project.md` §5).
- Cloud sign-in: invite yourself (`invite-pilot-user.md`), set a password, create an org, see it in `/app`.

## Notes
- **Every variable must be declared in `.railway/railway.ts`** (`preserve()` for secrets): `railway config apply` removes variables that exist only in the dashboard. Adding a secret = add `NAME: preserve()` in a PR, apply, then set the value in the dashboard.
- Railway project membership: owner only, at least until A18 is checked.
- Migrations are not run by Railway; `migrate.yml` owns them (expand/contract, ADR 0008).
- Rollback: Railway → service → Deployments → the last good one → Redeploy. Schema changes are not rolled back by this.
- `.railway/railway.ts` refuses any environment except `dev` until P1.
- Local integration tests (A6): GoTrue allows 30 sign-ins / 5 min / IP, so repeated runs can fail with 429 — wait 5 minutes. After laptop sleep, Docker's clock can drift > 30 s and tokens look expired: `supabase stop; supabase start`.
