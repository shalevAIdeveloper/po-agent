-- public.admin_add_member + private.admin_audit_log (0.4c-2 operator tool).
-- Fixture convention (ADR 0005): feed0000- ids, pgtap- slugs.
begin;
create extension if not exists pgtap with schema extensions;

select plan(26);

insert into auth.users (id, email, aud, role) values
  ('feed0000-0000-4000-8000-000000000061', 'owner61@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000062', 'Invited62@PgTap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000063', 'other63@pgtap.test', 'authenticated', 'authenticated'),
  -- Same address in different case: allowed by auth's case-sensitive unique index.
  ('feed0000-0000-4000-8000-000000000064', 'dup64@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000065', 'DUP64@pgtap.test', 'authenticated', 'authenticated');
-- SSO and banned users.
insert into auth.users (id, email, aud, role, is_sso_user) values
  ('feed0000-0000-4000-8000-000000000066', 'sso66@pgtap.test', 'authenticated', 'authenticated', true);
insert into auth.users (id, email, aud, role, is_anonymous) values
  ('feed0000-0000-4000-8000-000000000068', 'anon68@pgtap.test', 'authenticated', 'authenticated', true);
insert into auth.users (id, email, aud, role, banned_until) values
  ('feed0000-0000-4000-8000-000000000067', 'banned67@pgtap.test', 'authenticated', 'authenticated',
   now() + interval '1 day');

insert into public.organizations (id, name, slug, created_by) values
  ('feed0000-0000-4000-8000-0000000000a6', 'Acme Admin Tool', 'pgtap-admin-tool',
   'feed0000-0000-4000-8000-000000000061');
insert into public.memberships (org_id, user_id, role) values
  ('feed0000-0000-4000-8000-0000000000a6', 'feed0000-0000-4000-8000-000000000061', 'owner');

-- ===== privileges =====
select ok(
  has_function_privilege('service_role', 'public.admin_add_member(text,text,text,text,text)', 'EXECUTE'),
  'service_role can execute admin_add_member'
);
select ok(
  not has_function_privilege('authenticated', 'public.admin_add_member(text,text,text,text,text)', 'EXECUTE'),
  'authenticated cannot execute admin_add_member'
);
select ok(
  not has_function_privilege('anon', 'public.admin_add_member(text,text,text,text,text)', 'EXECUTE'),
  'anon cannot execute admin_add_member'
);
select ok(
  not has_function_privilege('po_api', 'public.admin_add_member(text,text,text,text,text)', 'EXECUTE'),
  'po_api cannot execute admin_add_member'
);
select ok(
  not has_table_privilege('service_role', 'private.admin_audit_log', 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
  and not has_table_privilege('authenticated', 'private.admin_audit_log', 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE'),
  'no API role can read or write the audit log directly'
);

-- authenticated is refused even when it is the org's owner.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000061","role":"authenticated"}', true);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', 'admin', 'owner', 'self-serve') $$,
  '42501', null, 'an org owner (authenticated) cannot call it'
);
reset role;

-- ===== happy path, as service_role =====
set local role service_role;
select results_eq(
  $$ select org_name, user_id, role
     from public.admin_add_member(' invited62@pgtap.test ', 'pgtap-admin-tool', 'reviewer',
                                  'ops@po-agent.test', 'Pilot: second buyer at Acme') $$,
  $$ values ('Acme Admin Tool'::text, 'feed0000-0000-4000-8000-000000000062'::uuid, 'reviewer'::text) $$,
  'adds the user (email matched case-insensitively, trimmed) and returns the result'
);
reset role;

select results_eq(
  $$ select role from public.memberships
     where org_id = 'feed0000-0000-4000-8000-0000000000a6'
       and user_id = 'feed0000-0000-4000-8000-000000000062' $$,
  $$ values ('reviewer'::text) $$,
  'membership exists with the given role'
);
select results_eq(
  $$ select action, actor, reason, org_id, target_user_id, details
     from private.admin_audit_log
     where target_user_id = 'feed0000-0000-4000-8000-000000000062' $$,
  $$ values ('add_member'::text, 'ops@po-agent.test'::text, 'Pilot: second buyer at Acme'::text,
             'feed0000-0000-4000-8000-0000000000a6'::uuid, 'feed0000-0000-4000-8000-000000000062'::uuid,
             '{"role": "reviewer"}'::jsonb) $$,
  'one audit row with actor, reason, ids and role (no email)'
);

-- ===== refusals (nothing written) =====
set local role service_role;
select throws_ok(
  $$ select * from public.admin_add_member('invited62@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'again please') $$,
  '23505', null, 'an existing member is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('nobody@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'typo test') $$,
  'P0002', null, 'unknown email is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-no-such-org', 'admin', 'ops', 'typo test') $$,
  'P0002', null, 'unknown org slug is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', 'owner', 'ops', 'make owner') $$,
  '22023', null, 'owner role is refused (ownership transfer is F7)'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', 'superuser', 'ops', 'bad role') $$,
  '22023', null, 'unknown role is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', 'admin', '  ', 'no actor') $$,
  '22023', null, 'blank actor is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', ' x ') $$,
  '22023', null, 'missing/short reason is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('other63@pgtap.test', 'pgtap-admin-tool', null, 'ops', 'null role') $$,
  '22023', null, 'null role is refused'
);
select throws_ok(
  $$ select * from public.admin_add_member('Dup64@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'ambiguous') $$,
  '21000', null, 'an email matching several users (case) is refused instead of picking one'
);
select throws_ok(
  $$ select * from public.admin_add_member('sso66@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'sso user') $$,
  'P0002', null, 'SSO users are not matched'
);
select throws_ok(
  $$ select * from public.admin_add_member('anon68@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'anonymous') $$,
  'P0002', null, 'anonymous users are not matched'
);
select throws_ok(
  $$ select * from public.admin_add_member('banned67@pgtap.test', 'pgtap-admin-tool', 'admin', 'ops', 'banned') $$,
  '22023', null, 'banned users are refused'
);
reset role;

select is(
  (select count(*)::int from public.memberships where user_id = 'feed0000-0000-4000-8000-000000000063'),
  0, 'refused calls added no membership'
);
select is(
  (select count(*)::int from private.admin_audit_log where org_id = 'feed0000-0000-4000-8000-0000000000a6'),
  1, 'refused calls wrote no audit rows'
);

-- ===== append-only =====
-- Row triggers fire only for matching rows: these rely on the audit row written above.
select throws_ok(
  $$ update private.admin_audit_log set reason = 'rewritten' where target_user_id = 'feed0000-0000-4000-8000-000000000062' $$,
  '42501', null, 'audit rows cannot be updated (even by the owner)'
);
select throws_ok(
  $$ delete from private.admin_audit_log where target_user_id = 'feed0000-0000-4000-8000-000000000062' $$,
  '42501', null, 'audit rows cannot be deleted (even by the owner)'
);
select throws_ok(
  $$ truncate private.admin_audit_log $$,
  '42501', null, 'audit log cannot be truncated'
);

select * from finish();
rollback;
