# syntax=docker/dockerfile:1
# Next.js standalone server. Build context: apps/web.
# Base image pinned by digest, pnpm by sha512 (corepack verifies it): a re-pushed tag
# or a tampered package must not end up in the build.

FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS deps
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable \
    && corepack install --global pnpm@12.18.0+sha512.9d7882d5dc82147ecfc4e062aa34834e0ad0296710a1837a1115ae012127d36b7109e321ac1598a1dd0a65597681b1e99917e12ed0d749eae91187943363beb3
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM deps AS build
COPY . .
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so they are
# build arguments (Railway passes service variables to declared ARGs). Public values
# only: never put a secret in a NEXT_PUBLIC_* variable.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
# Refuse before building: a secret key here would ship to every browser, and a missing
# or unresolved URL (empty, bare "https://", or a literal "${{api.RAILWAY_PUBLIC_DOMAIN}}"
# when the api domain doesn't exist yet) would bake a broken API address into the bundle.
# (Messages are printf-formatted so the smoke test matches real output, not the
# command text Docker echoes on failure.)
RUN refuse() { printf 'build refused: %s %s\n' "$1" "$2" >&2; exit 1; }; \
    case "$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY" in \
      sb_publishable_*) ;; \
      *) refuse NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY "must be an sb_publishable_ key" ;; \
    esac; \
    for pair in "NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL" \
                "NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL"; do \
      name="${pair%%=*}"; value="${pair#*=}"; \
      printf '%s' "$value" | grep -Eq '^https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:[0-9]{1,5})?$' \
        || refuse "$name" "must be an https:// origin (is the domain generated?)"; \
    done
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# Code stays owned by root (read-only for the runtime user); only Next's cache dir
# is writable.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
RUN mkdir -p .next/cache && chown node:node .next/cache
USER node
EXPOSE 3000
CMD ["node", "server.js"]
