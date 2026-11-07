-- Cross-org isolation: a user of org A can never read or change org B.
-- Fixture convention (ADR 0005): feed0000- ids, pgtap- slugs, @pgtap.test emails.
begin;
create extension if not exists pgtap with schema extensions;

select plan(25);

insert into auth.users (id, email, aud, role) values
  ('feed0000-0000-4000-8000-000000000001', 'owner-a@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000002', 'owner-b@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000003', 'reviewer-a@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000004', 'no-org@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000005', 'admin-a@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000006', 'two-orgs@pgtap.test', 'authenticated', 'authenticated');

insert into public.organizations (id, name, slug) values
  ('feed0000-0000-4000-8000-0000000001a0', 'Org A', 'pgtap-org-a'),
  ('feed0000-0000-4000-8000-0000000001b0', 'Org B', 'pgtap-org-b');

insert into public.memberships (org_id, user_id, role) values
  ('feed0000-0000-4000-8000-0000000001a0', 'feed0000-0000-4000-8000-000000000001', 'owner'),
  ('feed0000-0000-4000-8000-0000000001a0', 'feed0000-0000-4000-8000-000000000003', 'reviewer'),
  ('feed0000-0000-4000-8000-0000000001a0', 'feed0000-0000-4000-8000-000000000005', 'admin'),
  ('feed0000-0000-4000-8000-0000000001a0', 'feed0000-0000-4000-8000-000000000006', 'reviewer'),
  ('feed0000-0000-4000-8000-0000000001b0', 'feed0000-0000-4000-8000-000000000002', 'owner'),
  ('feed0000-0000-4000-8000-0000000001b0', 'feed0000-0000-4000-8000-000000000006', 'reviewer');

set local role authenticated;

-- ===== owner A =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000001","role":"authenticated"}', true);

select results_eq(
  $$ select id from public.organizations $$,
  $$ values ('feed0000-0000-4000-8000-0000000001a0'::uuid) $$,
  'owner A sees only org A'
);
select results_eq(
  $$ select user_id from public.memberships order by user_id $$,
  $$ values ('feed0000-0000-4000-8000-000000000001'::uuid), ('feed0000-0000-4000-8000-000000000003'::uuid),
            ('feed0000-0000-4000-8000-000000000005'::uuid), ('feed0000-0000-4000-8000-000000000006'::uuid) $$,
  'owner A sees only memberships of org A'
);
select results_eq(
  $$ with u as (update public.organizations set name = 'pwned'
                where id = 'feed0000-0000-4000-8000-0000000001b0' returning 1)
     select count(*)::int from u $$,
  $$ values (0) $$,
  'owner A cannot update org B (0 rows)'
);
select results_eq(
  $$ with u as (update public.organizations set name = 'Org A renamed', settings = '{"tz":"America/Chicago"}'
                where id = 'feed0000-0000-4000-8000-0000000001a0' returning 1)
     select count(*)::int from u $$,
  $$ values (1) $$,
  'owner A can update name and settings of org A'
);
select throws_ok(
  $$ update public.organizations set slug = 'pgtap-org-a2' where id = 'feed0000-0000-4000-8000-0000000001a0' $$,
  '42501', null, 'owner A cannot change the slug (no column privilege)'
);
select throws_ok(
  $$ update public.organizations set settings = '[]' where id = 'feed0000-0000-4000-8000-0000000001a0' $$,
  '23514', null, 'settings must be a JSON object'
);
select throws_ok(
  $$ update public.organizations
     set settings = (select jsonb_object_agg(g::text, md5(g::text)) from generate_series(1, 3000) g)
     where id = 'feed0000-0000-4000-8000-0000000001a0' $$,
  '23514', null, 'settings larger than 64 KB is rejected'
);
select throws_ok(
  $$ insert into public.organizations (name, slug) values ('Sneaky', 'pgtap-sneaky') $$,
  '42501', null, 'owner A cannot insert organizations directly'
);
select throws_ok(
  $$ delete from public.organizations where id = 'feed0000-0000-4000-8000-0000000001b0' $$,
  '42501', null, 'owner A cannot delete org B'
);
select throws_ok(
  $$ delete from public.organizations where id = 'feed0000-0000-4000-8000-0000000001a0' $$,
  '42501', null, 'owner A cannot delete org A from the client either'
);
select throws_ok(
  $$ insert into public.memberships (org_id, user_id, role)
     values ('feed0000-0000-4000-8000-0000000001b0', 'feed0000-0000-4000-8000-000000000001', 'owner') $$,
  '42501', null, 'owner A cannot add itself to org B'
);
select throws_ok(
  $$ update public.memberships set role = 'owner' where user_id = 'feed0000-0000-4000-8000-000000000003' $$,
  '42501', null, 'owner A cannot change roles from the client'
);
select throws_ok(
  $$ delete from public.memberships where org_id = 'feed0000-0000-4000-8000-0000000001b0' $$,
  '42501', null, 'owner A cannot delete memberships'
);

-- ===== admin A =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000005","role":"authenticated"}', true);

select results_eq(
  $$ with u as (update public.organizations set name = 'Org A (admin edit)'
                where id = 'feed0000-0000-4000-8000-0000000001a0' returning 1)
     select count(*)::int from u $$,
  $$ values (1) $$,
  'admin A can update org A'
);
select results_eq(
  $$ with u as (update public.organizations set name = 'pwned by admin'
                where id = 'feed0000-0000-4000-8000-0000000001b0' returning 1)
     select count(*)::int from u $$,
  $$ values (0) $$,
  'admin A cannot update org B (0 rows)'
);

-- ===== reviewer A =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000003","role":"authenticated"}', true);

select results_eq(
  $$ select id from public.organizations $$,
  $$ values ('feed0000-0000-4000-8000-0000000001a0'::uuid) $$,
  'reviewer A sees org A'
);
select is(
  (select count(*)::int from public.memberships where org_id <> 'feed0000-0000-4000-8000-0000000001a0'),
  0,
  'reviewer A sees no memberships outside org A'
);
select results_eq(
  $$ with u as (update public.organizations set name = 'reviewer edit'
                where id = 'feed0000-0000-4000-8000-0000000001a0' returning 1)
     select count(*)::int from u $$,
  $$ values (0) $$,
  'reviewer A cannot update org A (0 rows)'
);

-- ===== user in two orgs =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000006","role":"authenticated"}', true);

select results_eq(
  $$ select id from public.organizations order by id $$,
  $$ values ('feed0000-0000-4000-8000-0000000001a0'::uuid), ('feed0000-0000-4000-8000-0000000001b0'::uuid) $$,
  'two-org user sees exactly org A and org B'
);
select is(
  (select count(*)::int from public.memberships),
  6,
  'two-org user sees the memberships of both orgs (4 + 2)'
);

-- ===== owner B =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000002","role":"authenticated"}', true);

select results_eq(
  $$ select id from public.organizations $$,
  $$ values ('feed0000-0000-4000-8000-0000000001b0'::uuid) $$,
  'owner B sees only org B'
);
select results_eq(
  $$ select name from public.organizations $$,
  $$ values ('Org B'::text) $$,
  'org B was not changed by anyone from org A'
);

-- ===== authenticated user without any org =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000004","role":"authenticated"}', true);

select is_empty($$ select id from public.organizations $$, 'user without org sees no organizations');
select is_empty($$ select org_id from public.memberships $$, 'user without org sees no memberships');

reset role;

-- ===== anon =====
set local role anon;
select throws_ok($$ select id from public.organizations $$, '42501', null, 'anon cannot read organizations');
reset role;

select * from finish();
rollback;
