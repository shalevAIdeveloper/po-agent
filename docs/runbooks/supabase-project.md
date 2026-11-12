# Runbook — set up a hosted Supabase project (dev now, prod at P1)

The same steps create `po-agent-dev` (0.5b) and later `po-agent-prod` (pilot gate P1, Pro plan, org "PO Agent"). Replace `<env>` with `dev` or `production` and `<ref>` with the project ref (20 lowercase letters, from `https://supabase.com/dashboard/project/<ref>`; it isn't secret). dev: `ynscpsuhflxyklsfhdei`.

**Secrets never go into chat, files, commits or `$env:` lines** (PowerShell 5.1 keeps typed lines in its history file). They go into the Supabase dashboard, the GitHub web UI, Railway's dashboard, or the hidden prompts of the tools below, and live in your password manager.

## 1. Create the project (dashboard)
- Organization **PO Agent**. dev: Free plan. prod: Pro plan (P1).
- Name `po-agent-<env>`, region **East US (North Virginia)** = `us-east-1` (not `us-east-2`).
- Database password: generate a strong one and save it in the password manager.
- Security options:
  - **Enable Data API: on.** The operator script (`admin_add_member` RPC) and the e2e cleanup use it.
  - **Automatically expose new tables: off.** Our migrations revoke default grants and grant explicitly, so the cloud behaves like local.
  - **Enable automatic RLS: off.** Migrations enable and force RLS on every table themselves (pgTAP checks it); `db advisors` in `migrate.yml` flags any table without RLS.
- **Right after creating it:** Authentication → Sign In / Providers → turn **off** "Allow new users to sign up". Hosted projects allow sign-up by default; the rest of the Auth settings come in step 5.
- Free projects pause after about a week without activity: restore from the dashboard before running `migrate`.

## 2. GitHub environment `<env>`
Created (0.5b, approved): `dev` deploys only from branch `dev`, `production` only from `main`. `production` has no secret until P1, so `migrate` on `main` stops with a clear error.
- **Variable** `SUPABASE_PROJECT_REF` = `<ref>`.
- **One secret**, added by the owner in the web UI (Settings → Environments → `<env>` → Environment secrets → Add environment secret):
  - `SUPABASE_DB_URL`: dashboard → **Connect** → **Session pooler** → the URI for user `postgres.<ref>`, with `[YOUR-PASSWORD]` replaced by the database password. If the password has special characters, they must be percent-encoded (`@` → `%40`, `#` → `%23`, `/` → `%2F`, `?` → `%3F`, `:` → `%3A`, `%` → `%25`).
- **No Supabase access token:** a personal token reaches every project on the account, prod included (`sec` M1). The workflow uses only the database URL of its own environment.
- Never add `SUPABASE_*` as repository-level secrets: only environment secrets, so the branch rules apply.

## 3. Apply the migrations
GitHub → Actions → **migrate** → Run workflow:
- dev: branch `dev`. First run with **apply unticked** (dry run), read the plan, then run again with **apply ticked**. Afterwards every push to `dev` that changes `supabase/migrations/**` applies automatically.
- production: branch `main` only, by hand, the same dry run first.

The workflow checks:
- the URL belongs to this environment's project and doesn't weaken TLS;
- it refuses to push roles or seeds (A2);
- local and remote migration histories match;
- `db advisors` reports no errors.

Never run `supabase db push --include-roles` against a hosted project: it would push the local `po_api` password from `supabase/roles.sql`.

## 4. The API role `po_api` (A2)
Migrations create `po_api` **without a password**, so it can't log in until you set one.
1. **Don't type the password in the SQL editor**: Supabase saves editor queries as snippets, and logs may record the statement (`sec` M2).
2. From `apps/api`, let the tool generate it:
   ```powershell
   uv run python -m tools.set_api_role_password --generate --ca-file <path to prod-ca-2021.crt>
   ```
   - It asks (hidden) only for the `postgres` session-pooler connection string, the same one as `SUPABASE_DB_URL`.
   - After the paste it prints just `received N chars, starts with postgresql://: yes`, never the content. If it says `no`, or the length looks wrong, the paste didn't arrive whole; nothing has been sent yet.
   - It generates a 40-character password (letters and digits, so nothing needs encoding). It computes the SCRAM-SHA-256 verifier on your machine and sends only that.
   - It prints the password and the ready `po_api` connection string **once**, at the end. Save both in the password manager, then clear the screen and scrollback with `cls`.
   - Without `--generate` it asks for a password twice (24–72 printable ASCII characters, no spaces) and confirms only the length of each input.
3. The API's `DATABASE_URL` (Railway only, 0.5c) is the **session pooler** URL for user `po_api.<ref>` with that password: Supavisor session mode, port 5432, works over IPv4. The direct connection is IPv6-only.
4. Check it:
   ```powershell
   uv run python -m tools.check_api_role --ca-file <path to prod-ca-2021.crt>
   ```
   The CA certificate comes from Dashboard → Database → SSL Configuration → Download certificate. Without `--ca-file` the tool still encrypts, but doesn't verify the certificate.

   Every line must be PASS:
   - login (over TLS), PG ≥ 16;
   - login only, with no extra powers;
   - member of `authenticated` only (SET, not INHERIT);
   - no access of its own;
   - the switch to `authenticated` with RLS;
   - the 8 s / 15 s timeouts;
   - the local password `po_api_local_only` is **rejected** (an auth error, not a network error).

   "TEMP privilege" is informational (A4).
5. The rejection check makes one deliberate failed login. Running it many times in a row can get your IP banned by Supabase: Dashboard → Database → Network bans (or `supabase network-bans`) to lift it.

## 5. Auth settings (A14, D6)
`supabase/config.toml` holds the Auth settings: sign-up off, confirmations on, minimum 12, `secure_password_change`, `otp_length = 8`, TOTP enabled, the invite / recovery / magic-link templates. **`supabase config push` only for a project that has a `[remotes.<env>]` block** (`sec` M4):
- `config push` sends every property the file declares, local values included, and doesn't ask when run non-interactively.
- Without the overrides it would push `site_url` / redirect URLs pointing to `localhost`, so invite and reset links would go to localhost.
- It would also push `max_frequency = "1s"` with `email_sent = 2` per hour, so a few reset requests would use up the email quota.

The `[remotes.<env>]` block (dev: `[remotes.dev]`, added in A14; prod: add `[remotes.prod]` at P1) sets only:
- `project_id = "<ref>"`;
- `auth.site_url` and `auth.additional_redirect_urls`: the exact deployed web origin only (https, no localhost, no wildcards, no trailing slash);
- `auth.email.max_frequency = "60s"`.

**The hosted email rate limit is set by hand**, not by the file: the CLI pushes `auth.rate_limit.email_sent` only when `[auth.email.smtp]` is enabled in `config.toml`, and SMTP stays in the dashboard. Dashboard → Authentication → Rate Limits → "emails sent per hour" = **30** (needs custom SMTP); check it is still there after saving.

`apps/api/tests/test_supabase_config.py` pins this: the base hardening values; no `[auth.email.smtp]`, no hooks, no other sign-in providers / phone MFA / OAuth server in the base file (all of it would be pushed); the exact dev origin; and that a remote block overrides nothing but the URLs and `max_frequency` (so signup, confirmations, password and OTP rules can't be weakened per environment).

Then, **the owner, in their own terminal** — never an agent session, never with `--yes` (a non-interactive push proceeds without asking). No `link` needed: `--project-ref` targets the project directly. If the CLI says you are not logged in, run `supabase login` first (browser).
```powershell
cd C:\dev\po-agent                     # on an up-to-date `dev` that contains the remote block
supabase config diff --project-ref ynscpsuhflxyklsfhdei --exit-code
                                       # paste the output for review before anything is pushed
```
The ref must be one that has a `[remotes.*]` block: for any other ref the base values (localhost, 1s) are compared (first output line: `Comparing against project … using [remotes.dev]` — if it doesn't say `[remotes.dev]`, stop).

**dev: the diff is a check, not a push (decided 2026-11-12).** `config push` works per resource (auth, pooler, storage, …) and would also offer to push local pooler and storage defaults (Enter = yes). So Auth differences are fixed **by hand in the dashboard**, and `config diff` is re-run until only the accepted differences below remain. Exit code 2 is expected because of them.

**Accepted differences on `po-agent-dev`** (13, checked 2026-11-12; `class` as printed by the JSON output):

| Path | Class | Why it stays |
|---|---|---|
| `auth.email.smtp.{enabled, host, port, user, admin_email, sender_name}` | remote_only | SMTP lives in the dashboard (Resend); the file declares none of it, so nothing compares or pushes it |
| `auth.password_requirements` | local_only | `""` locally, unset in the cloud: both mean no composition rules (D6) |
| `auth.sms.twilio.enabled` | update (local false, remote true) | Twilio is the selected SMS provider, but phone sign-in is off (`auth.sms.enable_signup` matches `false`; Phone provider off in the dashboard) |
| `db.pooler.default_pool_size` / `max_client_conn` | update (20/100 local, 15/200 remote) | Supabase sets these per compute size; the file's values are local defaults |
| `storage.vector.enabled` | update (true local, false remote) | Vector buckets aren't used; not on the Free plan |
| `storage.vector.max_buckets` / `max_indexes` | local_only | Same |

**Stop and ask on anything else**, in particular:
- any `auth.*` difference not in the table (fix it in the dashboard, then re-run the diff);
- `site_url` showing localhost;
- an `auth.email.smtp.*` line whose class is **not** `remote_only` (the file would be overwriting SMTP);
- `db.network_restrictions`, MFA factors, providers, `[api]`.

`auth.rate_limit.email_sent` is not compared (listed as unmanaged): check it in the dashboard (30/h).

`config push` stays for later (e.g. prod at P1, with matching remote overrides for pooler/storage so only Auth differs). If it is ever used: the owner only, interactively, typing `n` to every resource not approved, then the diff again.
- **SMTP is set in the dashboard, not in `config.toml`** (Authentication → Emails → SMTP settings), by the owner:
  - Until P2: Resend's test sender. Host `smtp.resend.com`, port `465`, username `resend`, password = a Resend API key (sending access only), sender `onboarding@resend.dev`, name `PO Agent (dev)`. The test sender only delivers to the Resend account's own address, which is enough to test invites and resets in dev.
  - At P2: sender on `mail.<domain>` after SPF / DKIM / DMARC are verified in Resend, in both projects.
- On new Free projects, custom email templates work only with custom SMTP (A11): without SMTP, invite links won't reach `/auth/confirm`.
- **Email templates:** pushed by `config push` from `supabase/templates/*.html` once the remote block is in use (dev after A14). Before that (and for any project without a remote block) they are pasted by hand in the dashboard (Authentication → Email Templates). Invite, Reset Password and Magic Link must match `supabase/templates/*.html` (body without the HTML comments; subjects from `config.toml`). Any change to those files = paste it again in every hosted project.
  - Symptom of the **default** templates still in place (seen in 0.5c): the email link goes through `…supabase.co/auth/v1/verify` and lands on `<site_url>/#error=access_denied&error_code=otp_expired`. The default link spends the token on GET (scanners can use it up) and redirects to the Site URL. Ours links straight to `<site_url>/auth/confirm?token_hash=…`.
- **URL Configuration** (set by `config push` from the remote block; by hand only for a project without one): Site URL = the exact web origin (`https://web-dev-3fce.up.railway.app` for dev; no trailing slash, no path). Check it is still there after saving: in 0.5c it wasn't saved the first time. The app sends no `redirectTo` (templates build the link), so Redirect URLs only needs that same origin; no wildcards.
- Dev emails from `onboarding@resend.dev` land in spam (expected); fixed by the P2 sender domain.
- **Leaked password protection (A10):** Pro feature. Record "not available" for dev and enable it at P1.
- **Password minimum (A19):** Auth counts bytes, not characters; the web enforces 12 characters.

## 6. Verify
- `migrate` run green: histories match, advisors show no errors.
- `check_api_role`: all PASS.
- After 0.5c: `supabase config diff` shows only the accepted differences of §5; invite yourself (dashboard → Users → Invite) → the email arrives → the link opens `/auth/confirm` on the deployed web.

## Notes
- Never edit a migration that is already in `dev`; add a new one.
- `admin_add_member` and the audit log come with the migrations; the operator steps are in `invite-pilot-user.md`.
- The API's `DATABASE_URL` must use TLS (no weak `sslmode`), verified against the Supabase CA — enforced since 0.5c: outside local the API requires `DATABASE_CA_CERT` and connects verify-full (ADR 0008, `railway.md` §4).
