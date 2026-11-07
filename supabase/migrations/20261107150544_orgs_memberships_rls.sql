-- M0 0.3b: organizations + memberships with RLS (SPEC §3, §6, §10; ADR 0004).
--
-- Grants are explicit: hosted Supabase no longer grants anon/authenticated
-- anything on new public objects (2026-10-30), while the local CLI still does.
-- Revoking first and granting only what is needed makes both behave the same.

-- ---------------------------------------------------------------------------
-- Defaults for future objects created by `postgres` in public: no access for
-- anon/authenticated unless a migration grants it (matches hosted behavior).
-- EXECUTE for PUBLIC on functions is a global default and cannot be revoked
-- per schema, so every function below also revokes it explicitly.
-- ---------------------------------------------------------------------------
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on functions from anon, authenticated;

-- ---------------------------------------------------------------------------
-- private schema: helpers used by RLS policies. Not in the API's exposed
-- schemas (config.toml [api].schemas), so clients cannot call them as RPC.
-- ---------------------------------------------------------------------------
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
-- Policies run as the querying role, which needs USAGE to call the helpers.
grant usage on schema private to authenticated;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  -- Becomes part of the inbound address orders+<slug>@ in M6; validated, never transformed here.
  slug text not null unique,
  -- Non-secret preferences only: every member (incl. reviewers) can read it.
  -- Secrets (ERP credentials, webhook secrets) go in a service-role-only table.
  settings jsonb not null default '{}'::jsonb,
  -- Who created the org via create_organization(); used for the per-user creation cap.
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  constraint organizations_name_length check (char_length(btrim(name)) between 1 and 200),
  constraint organizations_slug_format check (
    char_length(slug) between 3 and 40 and slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
  ),
  -- Slugs become mailbox names in M6; keep system/role addresses unclaimable.
  constraint organizations_slug_not_reserved check (
    slug <> all (array[
      'abuse', 'admin', 'administrator', 'api', 'app', 'billing', 'dev', 'docs',
      'help', 'hostmaster', 'info', 'mail', 'mailer-daemon', 'no-reply', 'noreply',
      'orders', 'postmaster', 'prod', 'root', 'sales', 'security', 'staging',
      'status', 'support', 'system', 'test', 'webmaster', 'www'
    ])
  ),
  constraint organizations_settings_object check (jsonb_typeof(settings) = 'object'),
  constraint organizations_settings_size check (pg_column_size(settings) < 65536)
);

comment on table public.organizations is 'Tenant. Every tenant table carries org_id referencing this.';

create index organizations_created_by_idx on public.organizations (created_by);

create table public.memberships (
  org_id uuid not null references public.organizations (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role text not null,
  created_at timestamptz not null default now(),
  primary key (org_id, user_id),
  constraint memberships_role_valid check (role in ('owner', 'admin', 'reviewer'))
);

comment on table public.memberships is 'User ↔ organization with a role. A user may belong to several orgs.';

-- RLS helpers filter by user_id; the PK covers (org_id, user_id) lookups.
create index memberships_user_id_idx on public.memberships (user_id);

-- ---------------------------------------------------------------------------
-- Helpers. SECURITY DEFINER so a policy on memberships can read memberships
-- without recursing; owner `postgres` has BYPASSRLS. Empty search_path and
-- fully qualified names prevent search_path hijacking.
-- ---------------------------------------------------------------------------
create function private.is_org_member(p_org_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.memberships m
    where m.org_id = p_org_id
      and m.user_id = (select auth.uid())
  );
$$;

create function private.has_org_role(p_org_id uuid, p_roles text[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.memberships m
    where m.org_id = p_org_id
      and m.user_id = (select auth.uid())
      and m.role = any (p_roles)
  );
$$;

revoke all on function private.is_org_member(uuid) from public, anon, authenticated;
revoke all on function private.has_org_role(uuid, text[]) from public, anon, authenticated;
grant execute on function private.is_org_member(uuid) to authenticated;
grant execute on function private.has_org_role(uuid, text[]) to authenticated;

-- ---------------------------------------------------------------------------
-- Table privileges: least privilege, RLS decides which rows.
-- No client INSERT/DELETE: orgs are created via public.create_organization();
-- membership management goes through the API (F7).
-- ---------------------------------------------------------------------------
-- service_role too: default privileges grant it everything (incl. TRUNCATE) at creation.
revoke all on table public.organizations from anon, authenticated, service_role;
revoke all on table public.memberships from anon, authenticated, service_role;

grant select on table public.organizations to authenticated;
-- slug is deliberately not updatable by clients (it becomes an email address in M6).
grant update (name, settings) on table public.organizations to authenticated;
grant select on table public.memberships to authenticated;

-- service_role (bypasses RLS) is for trusted server-side jobs (the worker).
-- The API itself runs requests as `authenticated` so RLS applies (ADR 0003, planned for 0.4b).
-- No TRUNCATE: it would skip the row-level last-owner trigger.
grant select, insert, update, delete on table public.organizations to service_role;
grant select, insert, update, delete on table public.memberships to service_role;

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------
alter table public.organizations enable row level security;
alter table public.organizations force row level security;
alter table public.memberships enable row level security;
alter table public.memberships force row level security;

create policy organizations_select_member
  on public.organizations
  for select
  to authenticated
  using (private.is_org_member(id));

create policy organizations_update_admin
  on public.organizations
  for update
  to authenticated
  using (private.has_org_role(id, array['owner', 'admin']))
  with check (private.has_org_role(id, array['owner', 'admin']));

create policy memberships_select_member
  on public.memberships
  for select
  to authenticated
  using (private.is_org_member(org_id));
