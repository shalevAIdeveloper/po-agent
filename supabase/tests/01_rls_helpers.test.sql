-- private.is_org_member / private.has_org_role truth table.
-- Fixture convention (ADR 0005): ids start with feed0000-, slugs with pgtap-,
-- emails end in @pgtap.test, so fixtures never collide with local data.
begin;
create extension if not exists pgtap with schema extensions;

select plan(14);

insert into auth.users (id, email, aud, role) values
  ('feed0000-0000-4000-8000-000000000001', 'owner-a@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000002', 'owner-b@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000003', 'reviewer-a@pgtap.test', 'authenticated', 'authenticated'),
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
select ok(private.is_org_member('feed0000-0000-4000-8000-0000000001a0'), 'owner A is a member of org A');
select ok(not private.is_org_member('feed0000-0000-4000-8000-0000000001b0'), 'owner A is not a member of org B');
select ok(private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['owner']), 'owner A is owner of org A');
select ok(not private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['reviewer']), 'owner A is not reviewer of org A');
select ok(
  not private.has_org_role('feed0000-0000-4000-8000-0000000001b0', array['owner', 'admin', 'reviewer']),
  'owner A has no role in org B'
);

-- ===== admin A =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000005","role":"authenticated"}', true);
select ok(private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['owner', 'admin']), 'admin A passes an owner/admin check');
select ok(not private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['owner']), 'admin A is not owner');

-- ===== reviewer A =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000003","role":"authenticated"}', true);
select ok(private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['reviewer']), 'reviewer A is reviewer of org A');
select ok(not private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['owner', 'admin']), 'reviewer A is not owner/admin');

-- ===== user in two orgs =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000006","role":"authenticated"}', true);
select ok(private.is_org_member('feed0000-0000-4000-8000-0000000001a0'), 'two-org user is a member of org A');
select ok(private.is_org_member('feed0000-0000-4000-8000-0000000001b0'), 'two-org user is a member of org B');

-- ===== no identity: helpers answer false, not error =====
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
select ok(not private.is_org_member('feed0000-0000-4000-8000-0000000001a0'), 'no sub claim: not a member');

reset role;

-- ===== anon cannot reach the helpers at all =====
set local role anon;
select throws_ok(
  $$ select private.is_org_member('feed0000-0000-4000-8000-0000000001a0') $$,
  '42501', null, 'anon cannot call private.is_org_member'
);
select throws_ok(
  $$ select private.has_org_role('feed0000-0000-4000-8000-0000000001a0', array['owner']) $$,
  '42501', null, 'anon cannot call private.has_org_role'
);
reset role;

select * from finish();
rollback;
