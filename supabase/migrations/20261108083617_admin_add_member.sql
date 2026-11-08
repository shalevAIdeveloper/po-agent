-- M0 0.4c-2: operator tool to add an invited user to an existing organization,
-- until membership management exists in the product (F7). Called only by the
-- local admin script (apps/api/tools/add_member.py) with the service-role key.
-- Every call that changes data writes an audit row in the same transaction.

-- ---------------------------------------------------------------------------
-- Audit log: append-only, in `private` (not exposed by the Data API). No role
-- but the table owner can read or write it; rows are inserted by the
-- SECURITY DEFINER function below. Read it in Studio / psql as postgres.
-- Stores ids, not emails (no PII beyond what auth.users already holds).
-- ---------------------------------------------------------------------------
create table private.admin_audit_log (
  id bigint generated always as identity primary key,
  occurred_at timestamptz not null default now(),
  action text not null,
  -- Who ran the tool (operator's own email or name), as given to the script.
  actor text not null,
  reason text not null,
  org_id uuid,
  target_user_id uuid,
  details jsonb not null default '{}'::jsonb,
  constraint admin_audit_log_action_valid check (action in ('add_member')),
  constraint admin_audit_log_actor_length check (char_length(btrim(actor)) between 1 and 200),
  constraint admin_audit_log_reason_length check (char_length(btrim(reason)) between 3 and 500),
  constraint admin_audit_log_details_object check (jsonb_typeof(details) = 'object')
);

comment on table private.admin_audit_log is
  'Append-only audit of operator actions done outside the product (admin scripts).';

create index admin_audit_log_org_id_idx on private.admin_audit_log (org_id);
create index admin_audit_log_target_user_id_idx on private.admin_audit_log (target_user_id);

revoke all on table private.admin_audit_log from public, anon, authenticated, service_role;
revoke all on sequence private.admin_audit_log_id_seq from public, anon, authenticated, service_role;
alter table private.admin_audit_log enable row level security;
alter table private.admin_audit_log force row level security;

-- Append-only, even for roles that could otherwise write (defense in depth).
create function private.block_audit_change()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% is not allowed on %', tg_op, tg_table_name using errcode = '42501';
end;
$$;

revoke all on function private.block_audit_change() from public, anon, authenticated, service_role;

create trigger admin_audit_log_append_only
  before update or delete on private.admin_audit_log
  for each row
  execute function private.block_audit_change();

create trigger admin_audit_log_block_truncate
  before truncate on private.admin_audit_log
  for each statement
  execute function private.block_audit_change();

-- ---------------------------------------------------------------------------
-- admin_add_member: adds an existing auth user (invited or active) to an org
-- as admin or reviewer, and audits it. Owners are not added this way (ownership
-- transfer is D2 / F7).
--
-- In `public` so the script can call it through the Data API (RPC), but EXECUTE
-- is granted to service_role only. SECURITY DEFINER because service_role cannot
-- read auth.users or write the audit log itself.
-- ---------------------------------------------------------------------------
create function public.admin_add_member(
  p_email text,
  p_org_slug text,
  p_role text,
  p_actor text,
  p_reason text
)
returns table (org_id uuid, org_name text, user_id uuid, role text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_matches bigint;
  v_user_id uuid;
  v_org_id uuid;
  v_org_name text;
begin
  if p_role is null or p_role not in ('admin', 'reviewer') then
    raise exception 'role must be admin or reviewer' using errcode = '22023';
  end if;
  if char_length(btrim(coalesce(p_actor, ''))) not between 1 and 200 then
    raise exception 'actor is required' using errcode = '22023';
  end if;
  if char_length(btrim(coalesce(p_reason, ''))) not between 3 and 500 then
    raise exception 'reason is required (3-500 characters)' using errcode = '22023';
  end if;

  -- Exactly one match, or refuse: auth.users' email uniqueness is case-sensitive and
  -- excludes SSO users, so a case-insensitive lookup could otherwise pick any one.
  select count(*), (array_agg(u.id))[1] into v_matches, v_user_id
  from auth.users u
  where lower(u.email) = lower(btrim(p_email))
    and u.deleted_at is null
    and not u.is_sso_user
    and not coalesce(u.is_anonymous, false);
  if v_matches = 0 then
    raise exception 'no user with that email (invite them first)' using errcode = 'P0002';
  end if;
  if v_matches > 1 then
    raise exception 'several users match that email; resolve it in the dashboard first'
      using errcode = '21000';
  end if;
  if exists (select 1 from auth.users u where u.id = v_user_id and u.banned_until > now()) then
    raise exception 'that user is banned' using errcode = '22023';
  end if;

  -- Lock the org first (ADR 0003 lock order: org, then memberships).
  select o.id, o.name into v_org_id, v_org_name
  from public.organizations o
  where o.slug = p_org_slug
  for no key update;
  if v_org_id is null then
    raise exception 'no organization with that slug' using errcode = 'P0002';
  end if;

  if exists (
    select 1 from public.memberships m
    where m.org_id = v_org_id and m.user_id = v_user_id
  ) then
    raise exception 'user is already a member of this organization' using errcode = '23505';
  end if;

  insert into public.memberships (org_id, user_id, role)
  values (v_org_id, v_user_id, p_role);

  insert into private.admin_audit_log (action, actor, reason, org_id, target_user_id, details)
  values (
    'add_member', btrim(p_actor), btrim(p_reason), v_org_id, v_user_id,
    jsonb_build_object('role', p_role)
  );

  return query select v_org_id, v_org_name, v_user_id, p_role;
end;
$$;

revoke all on function public.admin_add_member(text, text, text, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.admin_add_member(text, text, text, text, text) to service_role;
