-- po_api (the API's login role, ADR 0003) must stay powerless on its own:
-- every API query has to switch to `authenticated` so RLS applies.
begin;
create extension if not exists pgtap with schema extensions;

select plan(9);

select ok(
  exists (select 1 from pg_roles where rolname = 'po_api' and rolcanlogin),
  'po_api exists and can log in'
);

select is_empty(
  $$ select rolname from pg_roles
     where rolname = 'po_api'
       and (rolsuper or rolbypassrls or rolcreaterole or rolcreatedb or rolreplication or rolinherit) $$,
  'po_api is NOINHERIT and has no superuser/bypassrls/createrole/createdb/replication'
);

select results_eq(
  $$ select r.rolname, m.inherit_option, m.set_option
     from pg_auth_members m
     join pg_roles r on r.oid = m.roleid
     join pg_roles u on u.oid = m.member
     where u.rolname = 'po_api' $$,
  $$ values ('authenticated'::name, false, true) $$,
  'po_api is a member of authenticated only, to SET it (never to inherit)'
);

select is_empty(
  $$ select c.relname from pg_class c
     join pg_namespace n on n.oid = c.relnamespace
     where n.nspname in ('public', 'private', 'auth', 'storage')
       and c.relkind in ('r', 'p', 'v', 'm')
       and (has_table_privilege('po_api', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
            or has_any_column_privilege('po_api', c.oid, 'SELECT, INSERT, UPDATE')) $$,
  'po_api has no table privileges of its own'
);

select is_empty(
  $$ select p.oid::regprocedure::text from pg_proc p
     join pg_namespace n on n.oid = p.pronamespace
     where n.nspname in ('public', 'private')
       and has_function_privilege('po_api', p.oid, 'EXECUTE') $$,
  'po_api cannot execute any public/private function without switching to authenticated'
);

select ok(not pg_has_role('po_api', 'postgres', 'MEMBER'), 'po_api cannot become postgres');
select ok(not pg_has_role('po_api', 'service_role', 'MEMBER'), 'po_api cannot become service_role');
select ok(not pg_has_role('po_api', 'anon', 'MEMBER'), 'po_api cannot become anon');

select results_eq(
  $$ select unnest(s.setconfig) collate "C" as setting
     from pg_db_role_setting s
     join pg_roles r on r.oid = s.setrole
     where r.rolname = 'po_api' and s.setdatabase = 0
     order by 1 $$,
  $$ values ('idle_in_transaction_session_timeout=15s'::text collate "C"),
            ('statement_timeout=8s'::text collate "C") $$,
  'po_api has its own statement and idle-in-transaction timeouts'
);

select * from finish();
rollback;
