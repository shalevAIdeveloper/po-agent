#!/usr/bin/env bash
# Builds the deploy images and checks how they start (CI `images` job; runs locally too).
# Usage: .github/scripts/smoke-images.sh   (from the repo root, Docker running)
set -euo pipefail
# Git Bash (Windows) would rewrite container paths like /etc/ssl/...; no effect on Linux.
export MSYS_NO_PATHCONV=1

API_IMAGE=po-agent-api:ci
WEB_IMAGE=po-agent-web:ci
LEAK=pw-SHOULD-NOT-LEAK

fail() { echo "FAIL: $*" >&2; exit 1; }
cleanup() { docker rm -f po-smoke-api po-smoke-worker po-smoke-web >/dev/null 2>&1 || true; }
trap cleanup EXIT

wait_log() { # container pattern -> waits up to 10 s for the pattern in its logs
  for _ in $(seq 1 20); do
    docker logs "$1" 2>&1 | grep -q "$2" && return 0
    sleep 0.5
  done
  return 1
}

wait_http() { # url -> waits up to 30 s for any HTTP answer
  for _ in $(seq 1 30); do
    curl -s -o /dev/null "$1" && return 0
    sleep 1
  done
  fail "no answer from $1"
}

echo "::group::build api"
docker build -t "$API_IMAGE" apps/api
echo "::endgroup::"

# Any real CA certificate works for the settings check (no DB connection is opened).
CA_PEM=$(docker run --rm --entrypoint cat "$API_IMAGE" /etc/ssl/certs/ISRG_Root_X1.pem)
CLOUD_ENV=(
  -e APP_ENV=dev
  -e "DATABASE_URL=postgresql://u:${LEAK}@db.invalid:5432/postgres?sslmode=require"
  -e "DATABASE_CA_CERT=${CA_PEM}"
  -e SUPABASE_URL=https://example.supabase.co
  -e WEB_ORIGIN=https://web.example.com
)

echo "--- api image: contents"
docker run --rm --entrypoint sh "$API_IMAGE" -c '
  test "$(id -u)" != 0 || { echo "runs as root"; exit 1; }
  test "$APP_ENV" = prod || { echo "APP_ENV default must be prod"; exit 1; }
  test "$OTEL_SDK_DISABLED" = true || { echo "OTEL_SDK_DISABLED must be true"; exit 1; }
  for p in /app/tools /app/tests /app/evals /app/.env /app/app/.env; do
    test ! -e "$p" || { echo "unexpected $p"; exit 1; }
  done
  python - <<PY
import importlib.util as u
for mod in ("opentelemetry.sdk", "fastapi_cli", "pytest"):
    assert u.find_spec(mod) is None, f"{mod} must not be installed"
PY
' || fail "api image contents"

echo "--- api image: refuses to start without settings"
if docker run --rm "$API_IMAGE" >/tmp/api-nosettings.log 2>&1; then
  fail "api started without settings"
fi
grep -q "missing required settings" /tmp/api-nosettings.log || fail "unexpected startup error"

echo "--- api image: refuses APP_ENV empty or local (would turn off TLS and docs hiding)"
for value in "" local; do
  if docker run --rm "${CLOUD_ENV[@]}" -e "APP_ENV=${value}" "$API_IMAGE" >/tmp/api-local.log 2>&1; then
    fail "api started with APP_ENV='${value}'"
  fi
  grep -q "APP_ENV must be dev or prod in a deployed image" /tmp/api-local.log \
    || fail "unexpected error for APP_ENV='${value}'"
done

echo "--- api: serves /health on \$PORT, hides docs, logs no secrets or query strings"
docker run -d --name po-smoke-api -p 18080:9123 -e PORT=9123 "${CLOUD_ENV[@]}" "$API_IMAGE" >/dev/null
wait_http http://127.0.0.1:18080/health
test "$(curl -s http://127.0.0.1:18080/health)" = '{"status":"ok"}' || fail "/health body"
curl -s -D - -o /dev/null http://127.0.0.1:18080/health | grep -qi '^server:' && fail "server header"
for path in /docs /openapi.json /redoc; do
  test "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18080$path")" = 404 \
    || fail "$path must be 404 outside local"
done
curl -s -o /dev/null "http://127.0.0.1:18080/me?token_hash=SECRET-QUERY"
docker logs po-smoke-api 2>&1 | grep -q '"event": "http.request"' || fail "no request log"
docker logs po-smoke-api 2>&1 | grep -E "$LEAK|SECRET-QUERY" && fail "secret in api logs"

echo "--- worker: starts, stops cleanly on SIGTERM"
docker run -d --name po-smoke-worker "${CLOUD_ENV[@]}" "$API_IMAGE" python -m app.worker >/dev/null
# Stop only once it runs (signal handlers installed), and give the log driver a moment
# to flush after the stop: a fixed sleep + one read was flaky (0.5c `qa`).
wait_log po-smoke-worker '"worker.started"' || fail "no worker.started"
docker stop -t 10 po-smoke-worker >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' po-smoke-worker)" = 0 || fail "worker exit code"
wait_log po-smoke-worker '"worker.stopped"' || fail "no worker.stopped"
docker logs po-smoke-worker 2>&1 | grep -q "$LEAK" && fail "secret in worker logs"

echo "::group::build web"
docker build -t "$WEB_IMAGE" \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.test \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=https://ref.supabase.co \
  --build-arg NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_placeholder \
  apps/web
echo "::endgroup::"

echo "--- web image: a secret key as the publishable key fails the build"
if docker build -q -t po-agent-web:bad-key \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.test \
  --build-arg NEXT_PUBLIC_SUPABASE_URL=https://ref.supabase.co \
  --build-arg NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_secret_not-a-real-key \
  apps/web >/tmp/web-bad-key.log 2>&1; then
  fail "web built with an sb_secret_ key"
fi
grep -q "must be an sb_publishable_ key" /tmp/web-bad-key.log || fail "unexpected web build error"

echo "--- web image: contents"
docker run --rm --entrypoint sh "$WEB_IMAGE" -c '
  test "$(id -u)" != 0 || { echo "runs as root"; exit 1; }
  test -f /app/server.js || { echo "no standalone server"; exit 1; }
  ! test -w /app/server.js || { echo "runtime user can rewrite server.js"; exit 1; }
  test -w /app/.next/cache || { echo ".next/cache must be writable"; exit 1; }
  if find /app -path /app/node_modules -prune -o \( -name ".env*" -o -name "*.test.*" -o -name e2e \) -print | grep -q .; then
    echo "unexpected files"; exit 1
  fi
' || fail "web image contents"

echo "--- web: serves /healthz on \$PORT, redirects /app without a session"
docker run -d --name po-smoke-web -p 18081:4567 -e PORT=4567 "$WEB_IMAGE" >/dev/null
wait_http http://127.0.0.1:18081/healthz
test "$(curl -s http://127.0.0.1:18081/healthz)" = '{"status":"ok"}' || fail "/healthz body"
# Checked on a page: route handlers like /healthz never send X-Powered-By.
curl -s -D - -o /dev/null http://127.0.0.1:18081/login | grep -qi '^x-powered-by:' && fail "x-powered-by"
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/app)" = 307 || fail "/app redirect"

echo "OK: images build and start as expected"
