-- Extensions used by SKU matching (SPEC F3): trigram fuzzy match and embeddings.
-- Installed in the `extensions` schema, never in `public` (keeps them off the Data API).
create extension if not exists pg_trgm with schema extensions;
create extension if not exists vector with schema extensions;
