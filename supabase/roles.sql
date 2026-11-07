-- LOCAL DEVELOPMENT ONLY. Applied by the Supabase CLI before migrations.
-- Well-known local password, like postgres:postgres. Hosted projects set the
-- po_api password out of band (0.5b); never reuse this value anywhere else.
create role po_api login noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication
  password 'po_api_local_only';
