-- Schema-wide guards. These also cover every FUTURE table, view and function:
-- a new migration that forgets RLS, leaks a grant to anon, ships a view that
-- bypasses RLS, or adds a SECURITY DEFINER function without a pinned
-- search_path fails here.
begin;
create extension if not exists pgtap with schema extensions;

select plan(7);

select is_empty(
  $$
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and not (c.relrowsecurity and c.relforcerowsecurity)
  $$,
  'every public table has RLS enabled and forced'
);

select is_empty(
  $$
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('public', 'private')
      and c.relkind in ('r', 'p', 'v', 'm')
      and (
        has_table_privilege('anon', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
        or has_any_column_privilege('anon', c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
      )
  $$,
  'anon has no table or column privileges in public/private'
);

select is_empty(
  $$
    select p.oid::regprocedure::text
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('public', 'private')
      and has_function_privilege('anon', p.oid, 'EXECUTE')
  $$,
  'anon cannot execute any function in public/private'
);

select is_empty(
  $$
    select p.oid::regprocedure::text
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('public', 'private')
      and p.prosecdef
      and not coalesce('search_path=""' = any (p.proconfig), false)
  $$,
  'every SECURITY DEFINER function pins search_path to empty'
);

select ok(
  not has_schema_privilege('anon', 'private', 'USAGE'),
  'anon has no USAGE on schema private'
);

-- Views run as their owner by default; `postgres` has BYPASSRLS.
select is_empty(
  $$
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'v'
      and not exists (
        select 1 from unnest(c.reloptions) as opt
        where lower(opt) in ('security_invoker=true', 'security_invoker=on', 'security_invoker=1')
      )
  $$,
  'every public view uses security_invoker'
);

-- TRUNCATE skips row-level triggers and RLS.
select is_empty(
  $$
    select c.relname || ' -> ' || r.rolname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    cross join (values ('anon'), ('authenticated'), ('service_role')) as r (rolname)
    where n.nspname = 'public'
      and c.relkind in ('r', 'p')
      and has_table_privilege(r.rolname, c.oid, 'TRUNCATE')
  $$,
  'no API role can TRUNCATE a public table'
);

select * from finish();
rollback;
