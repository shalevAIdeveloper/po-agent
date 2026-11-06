# Git flow (from the whiteboard)

```
feature/x ──► local tests ──► PR into dev ──► CI tests ──► deploy dev (Railway dev + Supabase dev)
                                                   │
                                                   ▼
                                    PR dev → main (release) ──► CI tests ──► deploy prod (Railway prod + Supabase prod)
```

## Daily loop (PowerShell)
```powershell
git checkout dev; git pull
git checkout -b feature/upload-endpoint
# ... work with Claude Code ...
cd apps/api; uv run ruff check .; uv run mypy app; uv run pytest; cd ../..
cd apps/web; pnpm lint; pnpm typecheck; pnpm test; cd ../..
git add -A; git commit -m "feat: upload endpoint"
git push -u origin feature/upload-endpoint
gh pr create --base dev --fill
```

## Release
```powershell
gh pr create --base main --head dev --title "release: <date>" --body "<what changed>"
```
Merge only after CI is green and you have clicked through dev.

## Rules
- Never push to dev/main directly (enable branch protection on GitHub).
- Never edit an applied migration.
- Hotfix: branch from main → PR to main → then merge main back into dev.
