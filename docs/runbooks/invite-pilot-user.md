# Runbook — invite a pilot user

Sign-up is closed (invite-only). Until F7 brings invitations and member management into the product, an operator invites users from the Supabase dashboard and, for a second person at the same distributor, adds them to the existing organization with an admin script.

Local and both cloud projects behave the same. Cloud invites need the custom email templates and custom SMTP (A11, 0.5b) — check that first.

## 1. Invite the first person at a distributor

1. Supabase dashboard → the project (`po-agent-dev` or `po-agent-prod`; locally Studio at http://127.0.0.1:54323) → **Authentication → Users → Invite user** → their work email.
2. They get "You've been invited to PO Agent". The link opens `/auth/confirm` with a **Continue** button. Nothing happens until they press it: email security scanners that open links don't use up the invite.
3. They choose a password (at least 12 characters, no other rules), then **onboarding**: they create the organization (name + short name) and become its **owner**.

Locally the email lands in Mailpit: http://127.0.0.1:54324.

## 2. Add a second person to an existing organization

Do this **before** they accept the invite if you can. A user who already belongs to an organization **never sees onboarding**: after choosing a password they go straight to `/app`. If they already accepted and created their own org by mistake, the script still adds them and they'll see both orgs; removing the extra org is a manual SQL cleanup (rare; do it with the owner's agreement).

1. Invite them (step 1.1). Don't wait for them to accept.
2. Find the org's short name (slug): dashboard → Table Editor → `organizations`, or ask the owner.
3. From `apps/api`, in PowerShell. The script **asks for the secret key** (Project Settings → API Keys, `sb_secret_…`) and doesn't show what you paste. Don't put the key in an `$env:` line: PowerShell 5.1 saves typed lines to its history file on disk.

   ```powershell
   cd C:\dev\po-agent\apps\api
   $env:ADMIN_SUPABASE_URL = "https://<project-ref>.supabase.co"   # local: http://127.0.0.1:54321
   uv run python -m tools.add_member `
     --email buyer2@distributor.com --org acme `
     --role reviewer `
     --actor you@yourcompany.com --reason "Pilot: second buyer at Acme (ticket #12)" `
     --confirm-host <project-ref>.supabase.co
   ```

   - `--role`: `reviewer` (reviews orders) or `admin`. **Not `owner`**: ownership transfer is F7 (D2).
   - `--confirm-host` is required for any non-local project and must match the URL's host, so a command copied from local can't silently run against prod.
   - Success prints `Added user <id> to '<org>' (<org id>) as reviewer. Audit row written.`
4. Never paste the secret key into chat, tickets or files.

Errors are explicit: `no user with that email (invite them first)`, `no organization with that slug`, `already a member`, `several users match that email`, `that user is banned`, `secret (service-role) key`, `not deployed on this project` (migrations not applied).

### What the script does

- Calls `public.admin_add_member` (migration `20261108083617_admin_add_member`), which only the service role can execute. In one transaction it adds the membership and writes `private.admin_audit_log` (who, why, when, org id, user id, role — no email). If either fails, neither happens.
- The audit log is append-only (updates, deletes and truncates are blocked for every role; the database owner `postgres` could still disable the triggers). It records **only what this script does**: the secret key can also write `memberships` directly through the Data API, which is not audited. That is why the key is used only through this script, by an operator, with a reason. Read the log in the SQL editor:

  ```sql
  select occurred_at, actor, reason, org_id, target_user_id, details
  from private.admin_audit_log order by occurred_at desc limit 50;
  ```

- It is an operator tool: it lives in `apps/api/tools/`, is never imported by the API or worker, and must not be in the deployed image (0.5c). **Replaced by the in-app invitations screen in F7** (Follow-up A16), then delete the script and revoke the function.

## 3. Expired or lost invite

Links work once and expire after **1 hour** (`otp_expiry`, shared with password reset). If they report "This link has expired or has already been used": invite the same email again from the dashboard. For someone who hasn't accepted yet this keeps the same user (and any membership added with the script) and sends a fresh link; checked locally. Don't delete and re-create the user: deleting removes their memberships.

## 4. Password reset

Users reset their own password: **Sign in → Forgot your password?** → email → link → **Continue** → new password. The page always answers "If an account exists…", so it never reveals who has an account. Same 1-hour, single-use link.

`/set-password` works only within **15 minutes** of opening an invite or reset link, and shows the account's email ("Setting the password for …"). A signed-in session alone can't change the password. If a user opens a link while someone else is signed in in the same browser, that session is signed out first.

## 5. Removing someone

Not in the product until F7. A sole owner can't be deleted (D7): transfer ownership or delete the org first. Ask before deleting anything in prod.
