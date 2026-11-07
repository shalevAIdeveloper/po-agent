-- M0 0.4b: dedicated login role for the API (ADR 0003).
--
-- po_api has NO privileges of its own (NOINHERIT) and no BYPASSRLS. Every API
-- transaction must `set local role authenticated` with the caller's verified
-- JWT claims, so RLS applies to API queries exactly as to any client. If code
-- forgets to switch roles, queries fail with "permission denied" (fail closed)
-- instead of silently bypassing RLS, as they would if the API used `postgres`.
--
-- No password here: locally supabase/roles.sql sets one; hosted projects set
-- it out of band (0.5b). Without a password the role cannot log in.
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'po_api') then
    create role po_api login noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication;
  end if;
end;
$$;

-- Membership only to switch into `authenticated` (SET), never to inherit its privileges.
grant authenticated to po_api with inherit false, set true;

-- Role-level settings apply at login only. `authenticated`'s own timeout does not
-- kick in when a transaction switches role, so po_api carries its own limits.
alter role po_api set statement_timeout = '8s';
alter role po_api set idle_in_transaction_session_timeout = '15s';
