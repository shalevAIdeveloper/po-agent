# PO Agent — Product Specification (v0.1)

> Working name: **PO Agent**. Rename freely; the name appears only in docs and UI copy.
> Owner: Shalev. Status: Draft for M0. This file is the single source of truth for scope.

---

## 1. Problem

US wholesale distributors (industrial/MRO, electrical, plumbing, building materials, foodservice) receive dozens to hundreds of purchase orders (POs) per day by email: PDF attachments, Excel files, scanned faxes, phone photos, or free text in the email body. Customer service reps (CSRs) re-type every line into the ERP. This is slow (2–5 min per order), error-prone (wrong SKU, wrong unit of measure, wrong price), and does not scale.

**Hard parts (our moat):**
- Customers use **their own part numbers** or free-text descriptions, not the distributor's SKU.
- **Units of measure** are ambiguous (case vs. each; "12" may mean 12 cases or 12 units).
- PO prices often **don't match** the customer's price list.
- **Ship-to addresses** vary per order.

## 2. Product in one sentence

An AI agent that turns any incoming PO into a validated, ERP-ready sales order that a CSR approves in seconds — and that gets more accurate with every correction.

## 3. Users & roles

| Role | Who | Can do |
|---|---|---|
| `reviewer` | CSR | Review, edit, approve/reject orders; confirm SKU matches |
| `admin` | Ops manager | Everything above + catalog/customer imports, rules, users, connectors |
| `owner` | Business owner | Everything above + billing, org deletion |

Multi-tenant from day one: every business is an `organization`.

## 4. Scope

### MVP (in scope)
- Intake: manual upload (M1–M3), then a dedicated forwarding email address per org (M6).
- Extraction of header + lines from PDF, image, XLSX/CSV, and email body.
- SKU matching with learned per-customer mappings.
- Validation rules (price, UoM, duplicates, unknown customer/ship-to).
- Review UI (split view: original document | extracted order).
- Export: CSV in a configurable column layout (M5). ERP API connectors come after MVP.
- Audit log of every change.

### Non-goals (explicit)
- **No automatic submission to an ERP without human approval.** Human-in-the-loop is a product principle, not a setting.
- No pricing, credit, legal, or purchasing advice. The agent flags discrepancies; humans decide.
- No EDI, no outbound messages to the distributor's customers, no SMS/calls in MVP.

## 5. Functional requirements

### F1 — Intake
- Upload one or many files (PDF, PNG/JPG/HEIC, XLSX, CSV, EML) via drag & drop.
- Store originals in a private storage bucket; compute SHA-256 to detect exact duplicates.
- M6: each org gets `orders+<orgslug>@<inbound-domain>`; inbound webhook creates a `document` per attachment (and one for the body if it contains order lines).
- Identify sender → customer via `customers.email_domains`.

### F2 — Extraction
- Async job: document → LLM (vision-capable) → JSON validated against a strict schema (Pydantic). See §9.
- Extract: customer name, customer PO number, PO date, requested ship date, ship-to, bill-to, notes, and lines (line no, customer part number, description, qty, UoM, unit price, extended price).
- Unknown/absent fields are `null`. **The model must never guess a value that is not in the document.**
- Store raw model output, parsed output, model id, prompt version, latency, token cost.
- Spreadsheets: parse deterministically first (openpyxl read-only for XLSX, the stdlib `csv` module for CSV; no pandas, owner decision 2026-11-16, ADR 0012), use the LLM only to map columns. The model returns cell references, never values; lines and header values are copied or normalized from the cells by code.

### F3 — SKU matching (core IP)
Order of resolution per line:
1. **Exact mapping**: `sku_mappings` for (customer, customer_part_number).
2. **Exact SKU**: customer wrote our SKU.
3. **Fuzzy**: trigram similarity on part number/description (`pg_trgm`).
4. **Semantic**: embedding similarity over catalog descriptions (`pgvector`).
- Return top 5 candidates with scores. Above threshold → auto-select (still shown for review).
- When a reviewer confirms or corrects a match, upsert into `sku_mappings` (`source='learned'`, increment `confirmed_count`). This is how accuracy compounds.

### F4 — Validation
Each rule produces a typed flag on the order or line (`severity: info|warning|error`):
- Price differs from price list beyond tolerance (default 2%).
- UoM not valid for product, or conversion needed (uses `uom_conversions`).
- Qty is not a multiple of pack size.
- Duplicate customer PO number for the same customer.
- Unknown customer / unknown ship-to.
- Line total ≠ qty × unit price; header total ≠ sum of lines.
- Product inactive.

### F5 — Review UI
- Inbox list: status, customer, PO#, received time, line count, flag count, confidence.
- Review screen: **left** = original document viewer (zoom, page nav, highlight the source region of the selected line when available); **right** = header form + lines grid.
- Per-line confidence chip (green ≥ 0.90, amber 0.60–0.89, red < 0.60; configurable).
- Inline SKU picker with candidates + catalog search.
- **Keyboard-first**: `J/K` next/prev line, `E` edit, `Enter` confirm line, `⌘/Ctrl+Enter` approve order, `/` search catalog, `?` shortcuts sheet.
- Approve / Reject (with reason) / Send back to queue.

### F6 — Export
- `ExportConnector` interface: `validate(order) -> issues`, `export(order) -> result`.
- MVP implementation: CSV with an org-configurable column template; downloadable and optionally emailed.
- Post-MVP: NetSuite, Acumatica, Business Central, Epicor Prophet 21 (choose first by customer discovery).

### F7 — Admin
- Import catalog (CSV/XLSX), customers, ship-to addresses, price lists, UoM conversions — with column mapping and dry-run preview.
- Manage users & roles; thresholds; export template.

### F8 — Audit
- Every create/update/approve/export writes an `audit_events` row with actor, entity, action, JSON diff.

## 6. Data model (Supabase Postgres)

All tenant tables carry `org_id uuid not null` and have **RLS enabled** with policies based on `memberships`.

| Table | Key columns |
|---|---|
| `organizations` | id, name, slug, settings jsonb, created_at |
| `memberships` | user_id → auth.users, org_id, role (`owner/admin/reviewer`) |
| `customers` | id, org_id, name, erp_customer_id, email_domains text[] |
| `customer_addresses` | id, org_id, customer_id, label, address jsonb, erp_ship_to_id |
| `products` | id, org_id, sku, description, base_uom, list_price, active, embedding vector |
| `uom_conversions` | id, org_id, product_id, uom, factor_to_base, pack_size |
| `price_lists` | id, org_id, customer_id, product_id, price, valid_from, valid_to |
| `sku_mappings` | id, org_id, customer_id, customer_part_number, customer_desc_norm, product_id, uom, source (`manual/learned/import`), confirmed_count, updated_at |
| `documents` | id, org_id, source (`upload/email`), storage_path, mime, sha256, sender_email, received_at, status |
| `extractions` | id, org_id, document_id, model, prompt_version, raw_output jsonb, parsed jsonb, latency_ms, cost_usd, created_at |
| `orders` | id, org_id, document_id, customer_id, customer_po_number, po_date, requested_date, ship_to jsonb, status, confidence, created_at, approved_by, approved_at |
| `order_lines` | id, org_id, order_id, line_no, raw_text, customer_part_number, description, qty, uom, unit_price, product_id, match_method, match_confidence, flags jsonb |
| `exports` | id, org_id, order_id, connector, payload jsonb, status, external_id, error |
| `audit_events` | id, org_id, actor_id, entity, entity_id, action, diff jsonb, created_at |
| `jobs` | id, org_id, type, payload jsonb, status, attempts, run_after, locked_at, last_error |

**Order status machine:** `received → extracting → needs_review → approved → exported` ; side states `failed`, `rejected`. Transitions are enforced in the API, never in the client.

## 7. Architecture

```
            ┌────────────┐        ┌─────────────────┐
 Browser ──►│  web       │──JWT──►│  api (FastAPI)  │──► Supabase Postgres (+pgvector, RLS)
            │  Next.js   │        └───────┬─────────┘──► Supabase Storage (private)
            └────────────┘                │ enqueue
                                          ▼
 Inbound email ──webhook──► api ──► jobs table ──► worker ──► LLM provider
                                                        └──► matching / validation
```

| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router), TypeScript strict, Tailwind CSS, shadcn/ui, TanStack Table, PDF.js viewer |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, `uv` for packaging |
| Worker | Same codebase as API, separate process (`python -m app.worker`), Postgres job queue using `FOR UPDATE SKIP LOCKED` |
| DB/Auth/Storage | Supabase (Postgres, Auth, Storage, pgvector, pg_trgm) |
| LLM | Provider-agnostic `LLMClient` interface; model ids from env; structured output |
| Hosting | Railway: services `web`, `api`, `worker` |
| CI | GitHub Actions |
| Observability | Sentry (web + api), structured JSON logs |

**Auth model:** web uses Supabase Auth. API verifies the Supabase JWT, resolves membership, and scopes every query by `org_id`. RLS is the second line of defense for any direct client reads. The service-role key exists only on the server (api/worker).

## 8. Environments & branching

| Env | Git branch | Supabase | Railway |
|---|---|---|---|
| local | `feature/*` | `supabase start` (Docker) | — |
| dev | `dev` | project `po-agent-dev` | environment `dev` |
| prod | `main` | project `po-agent-prod` | environment `production` |

Flow: `feature/*` (from `dev`) → local tests → PR into `dev` → CI → auto-deploy dev → PR `dev → main` (release) → CI → manual merge → auto-deploy prod.
- `main` and `dev` are protected: no direct pushes, CI must pass.
- Conventional Commits. Small PRs.
- DB changes only through `supabase/migrations`. Applied migrations are never edited. Migrations reach prod only via release.

## 9. AI rules

- Prompts live in `apps/api/app/prompts/<name>/v<N>.md` and are versioned; every extraction stores `prompt_version`.
- Output must validate against the Pydantic schema; on failure retry once with the validation error, then mark `failed`.
- **Documents are untrusted input.** Text inside a PO (e.g., "ignore previous instructions") is data. The extraction prompt says so explicitly; the model has no tools and no side effects.
- No value may be invented. Every extracted line keeps `raw_text` for traceability.
- **Eval set:** `apps/api/evals/fixtures/` holds ≥30 anonymized/synthetic POs with expected JSON. `uv run python -m evals.run` reports field-level accuracy. A prompt or model change may not lower accuracy.
- Cost and latency are logged per extraction; dashboard later.

## 10. Security & privacy

- RLS on every tenant table; tests assert cross-org access is impossible.
- Secrets only in Railway/Supabase env vars; `.env.example` documents names, never values.
- No document contents or customer PII in logs or Sentry breadcrumbs.
- Private storage bucket; signed URLs with short TTL.
- Rate limiting on upload and auth endpoints; file type and size validation (max 20 MB).
- Data retention setting per org (default: keep originals 365 days).
- SOC 2 readiness is a post-MVP goal; keep the audit log and access controls clean from day one.

## 11. Quality bar

- Python: ruff + mypy (strict on `app/`), pytest. TypeScript: strict, ESLint, Vitest; Playwright for critical flows (login → upload → review → approve → export).
- CI on every PR: lint, typecheck, unit tests, migration lint. Evals run on PRs that touch `prompts/`, `extraction/`, `llm/` or `evals/`, **from recorded model responses: no LLM calls and no API key in CI**. A live eval (manual `workflow_dispatch`, owner-approved) is mandatory before merging any prompt or model change, and its recordings and baseline ship in that PR.
- Real customer documents never enter git; they live only in the gitignored `apps/api/evals/fixtures/private/`.
- Definition of Done: tests pass, docs updated (`docs/progress.md`), security subagent reviewed the diff.

## 12. Design direction

Goal: **the best-feeling operations tool a CSR has ever used.** Calm, dense, fast.
- Benchmark category: modern pro tools (Linear, Superhuman, Vercel dashboard) — precision, speed, keyboard flow. Original visual identity; do not copy any product.
- Design tokens first (color, type scale, spacing 4px grid, radii, shadows, motion). Light and dark themes.
- One confident accent color; status colors reserved for confidence/flags only.
- Typography: a modern grotesk for UI, a monospaced face for SKUs, quantities, and prices (tabular numbers).
- Motion: 120–200 ms, purposeful; skeleton loaders, optimistic updates.
- Every screen has designed empty, loading, and error states.
- Accessibility: WCAG AA contrast, full keyboard support, visible focus.

## 13. Success metrics (pilot)

- Header field accuracy ≥ 97%, line field accuracy ≥ 95%, measured in the pilot on real POs (M1 records a synthetic baseline and enforces no-regression only).
- ≥ 80% of lines auto-matched correctly after two weeks with a customer.
- Median review time ≤ 45 seconds per order (vs. 2–5 minutes manual).

## 14. Milestones

| # | Milestone | Outcome |
|---|---|---|
| M0 | Foundations | Monorepo, CI, envs (local/dev/prod), auth, organizations |
| M1 | Extraction engine | Upload → structured JSON; eval set + accuracy report |
| M2 | Catalog & matching | Imports; 4-step matching; learned mappings |
| M3 | Review UI | Design system + inbox + review screen |
| M4 | Validation | All F4 rules with flags in UI |
| M5 | Export & audit | CSV connector, audit log |
| M6 | Email intake | Forwarding address per org |
| M7 | Pilot hardening | Sentry, rate limits, onboarding, retention |
| Later | Growth | Microsoft Graph/Gmail intake, ERP connectors, billing (Stripe), analytics |
