-- create_organization refuses to run outside READ COMMITTED: its per-user cap
-- (advisory lock, then count) could be raced under a snapshot taken before
-- the lock. Separate file because the isolation level is set at BEGIN.
-- Fixture convention (ADR 0005): feed0000- ids, pgtap- slugs.
begin isolation level repeatable read;
create extension if not exists pgtap with schema extensions;

select plan(3);

insert into auth.users (id, email, aud, role) values
  ('feed0000-0000-4000-8000-000000000011', 'rr@pgtap.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"feed0000-0000-4000-8000-000000000011","role":"authenticated"}', true);

select is(
  current_setting('transaction_isolation'),
  'repeatable read',
  'this file runs under REPEATABLE READ'
);
select throws_ok(
  $$ select public.create_organization('RR Co', 'pgtap-rr-co') $$,
  '25000',
  'create_organization requires READ COMMITTED isolation',
  'create_organization is refused under REPEATABLE READ'
);
select is_empty(
  $$ select 1 from public.organizations where slug = 'pgtap-rr-co' $$,
  'no organization was created'
);

reset role;

select * from finish();
rollback;
