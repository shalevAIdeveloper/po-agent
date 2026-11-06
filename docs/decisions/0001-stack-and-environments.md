# 0001 — Stack and environments

## Context
Solo developer, needs speed, low ops overhead, multi-tenant security, and a path to production for US customers.

## Decision
- Next.js + TypeScript (web), FastAPI + Python (api + worker), Supabase (Postgres, Auth, Storage, pgvector, pg_trgm), Railway hosting, GitHub Actions CI.
- Three environments: local / dev / prod, mapped to branches feature/* / dev / main and to separate Supabase projects.
- Postgres-backed job queue (FOR UPDATE SKIP LOCKED) instead of Redis for MVP.
- Provider-agnostic LLM client.

## Consequences
- One database to operate; fewer moving parts.
- Python owns AI/data logic; TypeScript owns UX.
- Queue may need replacing at high volume — revisit at >50k docs/month.
