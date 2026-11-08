# Git flow

```
feature/x ──► local checks ──► PR into dev ──► CI (ci-ok) ──► merge ──► deploy dev
                                                                         (Supabase dev migrations + Railway dev)
                                                    │
                                                    ▼
                         PR dev → main (release) ──► CI (ci-ok) ──► merge ──► prod: migrations by hand
                                                                              (workflow_dispatch on main) + Railway prod
```

Prod exists from pilot gate P1 on (`docs/progress.md`); until then `main` only receives releases.

## Daily loop (PowerShell)
```powershell
git switch dev; git pull --ff-only
git switch -c feature/upload-endpoint
# ... work with Claude Code ...
cd apps/api; uv run ruff check .; uv run mypy app tools; uv run pytest -q; cd ../..
cd apps/web; pnpm lint; pnpm typecheck; pnpm test; cd ../..
git add -A
git commit -F <message-file>      # check `git log -1` before pushing
git push -u origin feature/upload-endpoint
gh pr create --base dev --fill
gh pr checks --watch
```

After a squash merge: `git switch dev; git pull --ff-only`, check that the old branch has the same content as `dev` (`git diff --quiet <branch> dev`), then `git branch -D <branch>`.

## Release
```powershell
gh pr create --base main --head dev --title "release: <date>" --body "<what changed>"
```
Merge only after `ci-ok` is green and you have clicked through dev. Prod migrations then run only when started by hand: Actions → `migrate` → Run workflow on `main` (0.5b).

## Branch protection (0.5a, GitHub Pro)
`dev` and `main`:
- Changes only through pull requests; 0 approvals required (solo developer).
- Required status check: **`ci-ok`** only. It aggregates every CI job and treats path-filtered (skipped) jobs as passing, so the required check list never needs to change when jobs are added.
- No force pushes, no branch deletion; rules apply to admins too.

Check: `gh api repos/shalevAIdeveloper/po-agent/branches/dev/protection`. A direct `git push origin dev` must be rejected.

## Rules
- Never push to `dev`/`main` directly (enforced by branch protection).
- Never edit a migration that is already in `dev`; add a new one.
- Hotfix: branch from `main` → PR to `main` → then PR `main` back into `dev`.
- Secrets are never pasted into chat or committed: GitHub secrets are added in the web UI (Settings → Environments / Secrets).
