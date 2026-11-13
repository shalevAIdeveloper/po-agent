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
5. Project → Settings → Environments: **PR Environments off** and **Bot PR Environments off**. Railway creates those environments itself (`railway.ts` can't refuse them), copies `dev`'s variables into them (incl. `DATABASE_URL`) and deploys PR branches that haven't passed `ci-ok`. Recheck after §6.

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
railway link --project <project id> --environment dev    # the worktree has its own link
```
Print the exact upload list and check it both ways: it must **not** contain `.env*`, keys/certs, `supabase/roles.sql`, `node_modules`, `tools/`, `tests/`, `evals/`, and it **must** contain the code the build needs (for web: all of `src/`, including `src/lib/supabase/`). The owner sees it before the upload:
```bash
# Git Bash, from the worktree root; approximates Railway's filter: .gitignore + root and
# per-app .railwayignore (the same model as apps/api/tests/test_railway_upload.py)
for app in apps/api apps/web; do
  cat .railwayignore "$app/.railwayignore" > "$TEMP/railwayignore-combined"
  all=$(find "$app" -type f | sort)
  comm -23 <(printf '%s\n' "$all") \
    <(printf '%s\n' "$all" | git -c core.excludesFile="$TEMP/railwayignore-combined" check-ignore --no-index --stdin | sort)
done
```
Then, from the worktree. The path must be **absolute** (a relative one fails with "prefix not found"):
```powershell
railway --version                                  # IaC needs ≥ 5.42.1 (0.5c used 5.71.0)
railway up C:\dev\po-agent-deploy\apps\api --service api --environment dev --detach -m "0.5c first deploy"
railway up C:\dev\po-agent-deploy\apps\api --service worker --environment dev --detach -m "0.5c first deploy"
railway up C:\dev\po-agent-deploy\apps\web --service web --environment dev --detach -m "0.5c first deploy"
railway deployment list --service api --json     # follow the deployment id until SUCCESS
railway unlink                                     # still in the worktree
cd ..\po-agent; git worktree remove ..\po-agent-deploy
```
A deploy counts only when that deployment reports `SUCCESS` (not when the upload returns).

**After §6 this section is not used:** with autodeploy on, `railway up` would put code that hasn't passed CI into `dev` (and the next push silently replaces it). Use it again only after the Undo in §6. Rollback = Redeploy of an earlier deployment (Notes).

**Ignore rules are gitignore syntax.** A pattern like `supabase/` (no leading `/`) matches a folder of that name at *any* depth. In 0.5c it silently dropped `apps/web/src/lib/supabase/` and the web build failed with `Module not found '@/lib/supabase/proxy'`. Root-only folders in the root `.railwayignore` are therefore anchored (`/supabase/`, `/docs/`, …). `apps/api/tests/test_railway_upload.py` checks the rules against the tracked files in CI.

**`railway config plan/apply` on Windows with the npm-installed CLI:** the IaC SDK runs `railway --version` itself and fails on the npm `.cmd` shim ("requires CLI 5.42.1"). Point it at the real binary for that shell: `$env:_ = "$env:APPDATA\npm\node_modules\@railway\cli\bin\railway.exe"` and call that exe directly.

## 6. Autodeploy from `dev` (after the PR is merged)
Small PR: `GITHUB_AUTODEPLOY = true` in `.railway/railway.ts` → CI green → merge → `config plan` **from `dev`** (must match the plan shown in the PR) → owner approves → apply. Apply only after the merge: before it, Railway would track `dev` while `dev`'s file still says `false`, and the next plan from `dev` would try to undo it. From then on a push to `dev` deploys each service whose watch paths changed, only after the GitHub Actions runs on that commit passed (`checkSuites`: CI incl. `ci-ok`, and `migrate` when it runs — a failed migration blocks the deploy). Not literally *every* check suite: on 2026-11-12 a suite of the `claude` GitHub app stayed `queued` with no runs on the commit and Railway deployed anyway after CI completed — so the gate is our Actions workflows, and a check that must gate deploys has to be an Actions job (or `ci-ok`'s needs).

**Right after apply:**
- Connecting the repo may deploy `dev`'s head right away, possibly without waiting for CI. Treat it as a deploy: each service reaches `SUCCESS`, then §7.
- `config plan` should show 0 changes. Before apply it already showed `build.builder` (null → `DOCKERFILE`) and `deploy.restartPolicyType` (null → `ON_FAILURE`) on all three services. If they still show, don't wave them through: check the latest build log of each service (it must build from the Dockerfile) and Service → Settings (builder Dockerfile, restart policy On Failure). Only when both are right, record them here as a known display-only diff.
  - **Known display-only diff (verified 2026-11-11, after the apply of PR #12):** every plan shows these 6 lines (`build.builder` and `deploy.restartPolicyType` on api, worker, web) although the services use them: build logs show `load build definition from apps/<app>/Dockerfile` with the app folder as build context (`COPY pyproject.toml` / `COPY package.json` resolve there; the repo root has neither), and the owner saw restart policy On Failure (5 retries) on all three in the dashboard. Railway reports these defaults as `null`. Any *other* line in a plan, or these fields changing to something else, is real and must be read.
- Project → Settings → Environments: PR Environments and Bot PR Environments still off (§1).

**Mandatory gate check on the first autodeploy** (the first merge to `dev` that touches a watched path; changes only under `.railway/` or `docs/` match no watch pattern and deploy nothing, this PR's own merge included). While CI runs on that commit, the service's new deployment must show **WAITING**; it may start building only after `ci-ok` completed, and must then reach **SUCCESS**. Services whose paths didn't change get no deployment. Record what you saw (deployment id, WAITING seen, CI finish time vs. build start) in `docs/progress.md`. If it builds while CI is still running, the gate doesn't work: emergency stop (below) and fix before any other merge to `dev`.
- **Done 2026-11-12 (PR #13, `0f83202`): passed.** All three deployments were WAITING from creation (09:02:09Z) through the whole CI run; `ci-ok` completed success 09:05:33Z (run 09:05:34Z); worker INITIALIZING first seen 09:05:37Z, first build-log lines api 09:05:44Z / web 09:05:43Z (worker reused the build, container 09:05:54Z); all SUCCESS ~09:06Z. Details in `docs/progress.md`.
- **When watching a gate yourself:** the `ci-ok` check run exists only once all its needs finished (before that it is *absent*, not pending). Read CI status **after** reading the deployments in the same round, and re-read it before calling a bypass, otherwise a few-seconds-old CI status looks like one. In Windows PowerShell 5.1 don't pass a `--jq` filter containing double quotes to `gh` (PS strips them, the query fails, and a fallback value hides it — this caused a false ALERT on 2026-11-12); parse the JSON with `ConvertFrom-Json`, and treat a failed query as "unknown", never as "absent". Use the deployment's build-log timestamps (`railway logs <id> --service <s> --build --json`) to compare with `ci-ok`'s `completed_at`.

**When a deploy is skipped** (CI or `migrate` failed on that commit): after the fix is merged, its commit may touch no watched path (e.g. a migration-only fix), so the skipped code never deploys. Check each service's latest deployment against `dev`'s head; if one is behind, Railway → service → **Deploy latest commit**, then `SUCCESS` and §7. CI runs on `dev` are never cancelled (`cancel-in-progress` is PR-only in `ci.yml`), so a quick follow-up merge can't skip a deploy that way.

**Merge = deploy.** Only the owner merges PRs to `dev` (CLAUDE.md).

**Emergency stop:** Railway → each service → Settings → Source → **Disconnect** (stops new deploys at once; running deployments keep running). Then the Undo PR, so the next plan is clean.

**Undo:** set the flag back to `false` in a PR, plan, apply (services lose their GitHub source; deploy with `railway up` again, §5).

## 7. Verify
- `curl https://<api-domain>/health` → `{"status":"ok"}`; `/docs` → 404.
- `https://<web-domain>/healthz` → `{"status":"ok"}`; the home page shows "API is up".
- `railway logs --service worker --lines 50` contains `worker.started`.
- A18 (before any real invite goes through the cloud domain): request `https://<web-domain>/auth/confirm?token_hash=test-not-a-token&type=invite`, then check `railway logs --service web --http` (and the dashboard HTTP logs) for whether the query string is recorded. Record the result in `docs/progress.md` (A18).
- Supabase Auth (A14): `site_url` and redirect URLs point at the web domain (`supabase-project.md` §5).
- Cloud sign-in: invite yourself (`invite-pilot-user.md`), set a password, create an org, see it in `/app`.

### Rate limit check (A13, ADR 0009)
After the first deploy of the limiter, after any change to `apps/web/src/lib/rate-limit/` or `lib/auth/rate-limits.ts`, and after Railway networking changes (custom domain, CDN). From the owner's machine (no account, no email; Auth sees 10 failed verifications):
```powershell
cd C:\dev\po-agent\apps\web
$env:DEPLOYED_WEB_URL="https://web-dev-3fce.up.railway.app"; pnpm test:deployed
```
- **Pass:** 11 attempts, each with a different forged `X-Real-IP` / `X-Forwarded-For`; 1–10 say "expired" (answered by Auth), the 11th is refused **by our limiter** (`[app limit]`). The forged headers didn't create new buckets.
- **Fail, 11th says "expired":** the forged header was trusted, so the per-IP limits can be dodged. Pilot gate A13 is open again; stop and fix before inviting anyone.
- **Fail fast, "Attempt 1 was already refused":** this IP used its budget in the last 5 minutes (an earlier run, or another device behind the same public IP — mobile carriers put many phones behind one shared address). Wait 5 minutes and re-run.
- **Fail, an early attempt says "Too many" without `[app limit]`:** Auth's own shared bucket was busy. Wait and re-run.
- The test prints `run started <time>` and the 11 results, to match with the logs below.

**A pass proves one shared bucket, not that it is *your* bucket.** That needs a **second client** and the logs:
1. Run 1: from the owner's machine (above).
2. Run 2: from **GitHub's network** — Actions → **Deployed check** → Run workflow (branch `dev`), or `gh workflow run deployed-check.yml --ref dev`. It runs the same test from a GitHub runner, against the fixed dev URL, with no secrets (`.github/workflows/deployed-check.yml`). Only after the web deploy of the current `dev` commit has finished: the workflow's first step refuses to start while CI or `migrate` on that commit is still running (a dispatched run is a check suite on the commit, which Railway's gate waits for), but it can't see the deploy itself. Each run uses 10 of Auth's shared 30 verifications / 5 min: don't run more than two runs within 5 minutes, or `/auth/confirm` is blocked for every dev user for a few minutes. No hotspot needed: 2026-11-13, hotspot runs came out on the home IP or on a carrier's shared IP and were hard to read.
3. **No deploy or restart of `web` between the two runs:** tags compare only within one process. `railway deployment list --service web --environment dev` shows the same active deployment before and after.
4. **The logs, per run**: start run 2 at least **3 minutes after** run 1 started, so the two windows don't overlap. Fill in both `run started` times (run 1 from your terminal, run 2 from the Actions log). The snippet prints counts and tags only, never an IP:
   ```powershell
   $runs = [ordered]@{ 'run 1' = [datetime]'<run 1 started, e.g. 2026-11-13T12:00:00Z>'; 'run 2' = [datetime]'<run 2 started>' }
   $posts = @(railway logs --service web --environment dev --http --method POST --path /auth/confirm --since 2h --json | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} })
   $events = @(railway logs --service web --environment dev --since 2h --json | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Where-Object { $_.event -like 'rate_limit.*' })
   foreach ($name in $runs.Keys) {
     $from = $runs[$name]; $to = $from.AddMinutes(3)
     $inRun = { param($t) ([datetime]$t) -ge $from -and ([datetime]$t) -lt $to }
     $p = @($posts | Where-Object { & $inRun $_.timestamp })
     $tags = @($events | Where-Object { $_.event -eq 'rate_limit.refused' -and (& $inRun $_.timestamp) } | ForEach-Object key_tag | Sort-Object -Unique)
     $unknown = @($events | Where-Object { $_.event -eq 'rate_limit.ip_unknown' -and (& $inRun $_.timestamp) }).Count
     "${name}: POSTs $($p.Count), source IPs $(@($p | ForEach-Object srcIp | Sort-Object -Unique).Count), ip_unknown $unknown, refused tags [$($tags -join ', ')]"
   }
   "source IPs across both runs: $(@($posts | Where-Object { $t = [datetime]$_.timestamp; $runs.Values | Where-Object { $t -ge $_ -and $t -lt $_.AddMinutes(3) } } | ForEach-Object srcIp | Sort-Object -Unique).Count)"
   ```

**Pass:** both runs pass, and for **each** run: 11 POSTs, 1 source IP, 0 `ip_unknown`, exactly **one** refused tag; the two runs' tags are **different**, and there are 2 source IPs across both runs. Timing between the runs doesn't matter beyond the 3 minutes: the tags, not the 5-minute window, show whether two clients got two buckets. **Fail:** two source IPs but the same tag in both runs, or any `ip_unknown` → the app isn't keying on the real client IP: A13 open, stop. One source IP across both runs → not two networks; repeat run 2.

`rate_limit.refused` lines carry the action, the limit (`ip`, `ip_wide`, `account`, `address`), the address family and, for IP limits only, `key_tag`: a 12-hex salted HMAC of the bucket key (per process: tags compare only within one deployment's lifetime; never the IP). At most 60 lines a minute; `dropped` counts the rest.
- Checked to work both ways against a local production server (2026-11-12/13): forged IPs trusted → fails; one fixed IP → passes.
- Afterwards your IP is limited on `/auth/confirm` for 5 minutes.

## Notes
- **The web service must stay at one replica** while the auth rate limits are in memory (ADR 0009). Before raising `replicas` for `web`, move the limiter to a shared store.
- **Every variable must be declared in `.railway/railway.ts`** (`preserve()` for secrets): `railway config apply` removes variables that exist only in the dashboard. Adding a secret = add `NAME: preserve()` in a PR, apply, then set the value in the dashboard.
- Railway project membership: owner only, at least until A18 is checked.
- Migrations are not run by Railway; `migrate.yml` owns them (expand/contract, ADR 0008).
- Rollback: Railway → service → Deployments → the last good one → Redeploy (never `railway up` once §6 is on). Schema changes are not rolled back by this.
- `.railway/railway.ts` refuses any environment except `dev` until P1.
- Local integration tests (A6): GoTrue allows 30 sign-ins / 5 min / IP, so repeated runs can fail with 429 — wait 5 minutes. After laptop sleep, Docker's clock can drift > 30 s and tokens look expired: `supabase stop; supabase start`.
