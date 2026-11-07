-- M0 0.3c: atomic org creation + last-owner guard.

-- ---------------------------------------------------------------------------
-- create_organization: the only way a client creates an org. Inserts the org
-- and the caller's owner membership in one transaction. SECURITY DEFINER
-- because clients have no INSERT on either table; it checks the caller itself.
-- Exposed as RPC on purpose (the API calls it under the user's identity).
-- ---------------------------------------------------------------------------
create function public.create_organization(p_name text, p_slug text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  -- Creation cap per user (not a membership cap): stops org/slug spam.
  c_max_created_orgs constant int := 5;
  v_user_id uuid := (select auth.uid());
  v_org_id uuid;
begin
  if v_user_id is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;

  -- Anonymous sign-ins also get the `authenticated` role; they may not own orgs.
  if coalesce(((select auth.jwt()) ->> 'is_anonymous')::boolean, false) then
    raise exception 'anonymous users cannot create organizations' using errcode = '42501';
  end if;

  -- The cap below (lock, then count) is race-free only under READ COMMITTED,
  -- where the count sees orgs committed while this call waited for the lock.
  -- Under REPEATABLE READ / SERIALIZABLE the snapshot predates the lock, so
  -- refuse loudly instead of letting concurrent calls exceed the cap.
  if pg_catalog.current_setting('transaction_isolation') <> 'read committed' then
    raise exception 'create_organization requires READ COMMITTED isolation'
      using errcode = '25000';
  end if;

  -- Serialize concurrent calls by the same user so the cap cannot be raced.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('create_organization:' || v_user_id::text, 0)
  );

  if (select count(*) from public.organizations o where o.created_by = v_user_id)
     >= c_max_created_orgs then
    raise exception 'organization limit reached (% per user)', c_max_created_orgs
      using errcode = '53400';
  end if;

  insert into public.organizations (name, slug, created_by)
  values (p_name, p_slug, v_user_id)
  returning id into v_org_id;

  insert into public.memberships (org_id, user_id, role)
  values (v_org_id, v_user_id, 'owner');

  return v_org_id;
end;
$$;

revoke all on function public.create_organization(text, text) from public, anon, authenticated, service_role;
grant execute on function public.create_organization(text, text) to authenticated;

-- ---------------------------------------------------------------------------
-- Every org keeps at least one owner. Blocks deleting or demoting the last
-- owner (or moving it to another org), unless the org itself is being deleted.
--
-- SECURITY DEFINER: the org lookup and owner count must see every row. As the
-- caller, RLS could hide the org row and the guard would wrongly conclude
-- "org is being deleted" and let the last owner go.
-- ---------------------------------------------------------------------------
create function private.ensure_org_keeps_an_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.role <> 'owner' or (tg_op = 'UPDATE' and new.role = 'owner' and new.org_id = old.org_id) then
    return coalesce(new, old);
  end if;

  -- A no-op UPDATE (not just FOR UPDATE) locks the org row: concurrent removals
  -- of two co-owners serialize under READ COMMITTED and fail with a
  -- serialization error under REPEATABLE READ instead of a write skew.
  -- No row: the org is being deleted and the membership goes with it.
  update public.organizations o set id = o.id where o.id = old.org_id;
  if not found then
    return coalesce(new, old);
  end if;

  if not exists (
    select 1
    from public.memberships m
    where m.org_id = old.org_id
      and m.role = 'owner'
      and m.user_id <> old.user_id
  ) then
    raise exception 'organization must keep at least one owner' using errcode = '23514';
  end if;

  return coalesce(new, old);
end;
$$;

revoke all on function private.ensure_org_keeps_an_owner() from public, anon, authenticated;

create trigger memberships_keep_an_owner
  before update or delete on public.memberships
  for each row
  execute function private.ensure_org_keeps_an_owner();

-- TRUNCATE skips row triggers, so it would bypass the guard above.
create function private.block_truncate()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'TRUNCATE is not allowed on %', tg_table_name using errcode = '42501';
end;
$$;

revoke all on function private.block_truncate() from public, anon, authenticated;

create trigger memberships_block_truncate
  before truncate on public.memberships
  for each statement
  execute function private.block_truncate();
