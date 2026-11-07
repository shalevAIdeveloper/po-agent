-- public.create_organization (validation, identity, per-user cap) and the
-- last-owner guard. Fixture convention (ADR 0005): feed0000- ids, pgtap- slugs.
begin;
create extension if not exists pgtap with schema extensions;

select plan(24);

insert into auth.users (id, email, aud, role) values
  ('feed0000-0000-4000-8000-000000000001', 'creator@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000007', 'anonymous@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000008', 'second-owner@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000009', 'cap@pgtap.test', 'authenticated', 'authenticated'),
  ('feed0000-0000-4000-8000-000000000010', 'member-of-many@pgtap.test', 'authenticated', 'authenticated');

set local role authenticated;

-- ===== creation and validation =====
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000001","role":"authenticated"}', true);

select lives_ok(
  $$ select public.create_organization('Acme Supply', 'pgtap-acme') $$,
  'authenticated user can create an organization'
);
select results_eq(
  $$ select o.slug, m.role, o.created_by
     from public.organizations o
     join public.memberships m on m.org_id = o.id
     where m.user_id = 'feed0000-0000-4000-8000-000000000001' $$,
  $$ values ('pgtap-acme'::text, 'owner'::text, 'feed0000-0000-4000-8000-000000000001'::uuid) $$,
  'creator becomes owner, sees the org, and is recorded as created_by'
);
select throws_ok(
  $$ select public.create_organization('Acme Again', 'pgtap-acme') $$,
  '23505', null, 'duplicate slug is rejected'
);
select throws_ok(
  $$ select public.create_organization('Bad Slug', 'PgTap Acme') $$,
  '23514', null, 'slug with uppercase/spaces is rejected (validated, not transformed)'
);
select throws_ok(
  $$ select public.create_organization('x', 'ab') $$,
  '23514', null, 'slug shorter than 3 chars is rejected'
);
select throws_ok(
  $$ select public.create_organization('   ', 'pgtap-blank-name') $$,
  '23514', null, 'blank name is rejected'
);
select throws_ok(
  $$ select public.create_organization('Postmaster Inc', 'postmaster') $$,
  '23514', null, 'reserved slug is rejected'
);

-- ===== identity checks =====
select set_config('request.jwt.claims', '{"role":"authenticated"}', true);
select throws_ok(
  $$ select public.create_organization('Nobody', 'pgtap-nobody') $$,
  '42501', 'not authenticated', 'missing sub claim is rejected'
);

select set_config(
  'request.jwt.claims',
  '{"sub":"feed0000-0000-4000-8000-000000000007","role":"authenticated","is_anonymous":true}',
  true
);
select throws_ok(
  $$ select public.create_organization('Anon Co', 'pgtap-anon-co') $$,
  '42501', 'anonymous users cannot create organizations', 'anonymous users cannot create organizations'
);

reset role;
set local role anon;
select throws_ok(
  $$ select public.create_organization('Anon Role', 'pgtap-anon-role') $$,
  '42501', null, 'anon role cannot execute create_organization'
);
reset role;

-- ===== per-user creation cap (5) =====
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000009","role":"authenticated"}', true);

select lives_ok(
  $$ select public.create_organization('Cap ' || g, 'pgtap-cap-' || g) from generate_series(1, 5) g $$,
  'a user can create 5 organizations'
);
select throws_ok(
  $$ select public.create_organization('Cap 6', 'pgtap-cap-6') $$,
  '53400', 'organization limit reached (5 per user)', 'the 6th organization is refused'
);
reset role;

-- The cap counts orgs a user CREATED, not orgs they belong to.
insert into public.organizations (id, name, slug)
select ('feed0000-0000-4000-8000-0000000002' || lpad(g::text, 2, '0'))::uuid, 'Other ' || g, 'pgtap-other-' || g
from generate_series(1, 6) g;
insert into public.memberships (org_id, user_id, role)
select ('feed0000-0000-4000-8000-0000000002' || lpad(g::text, 2, '0'))::uuid,
       'feed0000-0000-4000-8000-000000000010', 'reviewer'
from generate_series(1, 6) g;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000010","role":"authenticated"}', true);
select lives_ok(
  $$ select public.create_organization('Own Org', 'pgtap-own-org') $$,
  'a member of 6 orgs can still create their own'
);
reset role;

-- ===== last-owner guard (as postgres: the server/admin path) =====
select throws_ok(
  $$ delete from public.memberships m using public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.user_id = 'feed0000-0000-4000-8000-000000000001' $$,
  '23514', 'organization must keep at least one owner', 'cannot delete the last owner'
);
select throws_ok(
  $$ update public.memberships m set role = 'admin' from public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.user_id = 'feed0000-0000-4000-8000-000000000001' $$,
  '23514', 'organization must keep at least one owner', 'cannot demote the last owner'
);
select throws_ok(
  $$ update public.memberships m set org_id = 'feed0000-0000-4000-8000-000000000201'
     from public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.user_id = 'feed0000-0000-4000-8000-000000000001' $$,
  '23514', 'organization must keep at least one owner', 'cannot move the only owner to another org'
);
select throws_ok(
  $$ delete from auth.users where id = 'feed0000-0000-4000-8000-000000000001' $$,
  '23514', 'organization must keep at least one owner',
  'deleting the user who is the last owner is blocked (transfer ownership first)'
);
select throws_ok(
  $$ truncate public.memberships cascade $$,
  '42501', null, 'TRUNCATE on memberships is blocked (it would skip the guard)'
);

insert into public.memberships (org_id, user_id, role)
select id, 'feed0000-0000-4000-8000-000000000008', 'owner' from public.organizations where slug = 'pgtap-acme';

select throws_ok(
  $$ delete from public.memberships m using public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.role = 'owner' $$,
  '23514', 'organization must keep at least one owner', 'deleting all owners in one statement is blocked'
);

-- The guard must not depend on what the caller can see (RLS). Simulate a
-- future client delete policy (F7) and let owner 1 delete every membership.
grant delete on public.memberships to authenticated;
create policy pgtap_tmp_delete_by_admins on public.memberships
  for delete to authenticated
  using (private.has_org_role(org_id, array['owner', 'admin']));

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000001","role":"authenticated"}', true);
select throws_ok(
  $$ delete from public.memberships
     where org_id = (select id from public.organizations where slug = 'pgtap-acme') $$,
  '23514', 'organization must keep at least one owner',
  'an owner deleting all memberships via RLS cannot remove the last owner'
);
reset role;

select lives_ok(
  $$ update public.memberships m set role = 'admin' from public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.user_id = 'feed0000-0000-4000-8000-000000000001' $$,
  'an owner can be demoted when another owner exists'
);
select throws_ok(
  $$ delete from public.memberships m using public.organizations o
     where o.id = m.org_id and o.slug = 'pgtap-acme' and m.user_id = 'feed0000-0000-4000-8000-000000000008' $$,
  '23514', 'organization must keep at least one owner', 'the remaining owner is now protected'
);
select lives_ok(
  $$ delete from public.organizations where slug = 'pgtap-acme' $$,
  'deleting the org cascades its memberships, including the last owner'
);
select is_empty(
  $$ select 1 from public.memberships m
     where m.user_id in ('feed0000-0000-4000-8000-000000000001', 'feed0000-0000-4000-8000-000000000008') $$,
  'memberships are gone with the org'
);

select * from finish();
rollback;
