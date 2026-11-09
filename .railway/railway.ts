// Railway infrastructure for PO Agent (ADR 0008, runbook docs/runbooks/railway.md).
//
// Preview:  cd .railway; railway config plan    (values redacted)
// Apply:    only after the owner approved that exact plan.
//
// Rules: no Railway UUIDs or generated *.up.railway.app domains here; secrets are
// never written here: they use preserve() and are set by the owner in the dashboard.
// Every variable a service has must be declared here (preserve() for secrets): `apply`
// removes variables that exist only in the dashboard.
import { defineRailway, github, preserve, project, service } from "railway/iac";

const REPO = "shalevAIdeveloper/po-agent";

// false until the Dockerfiles are on `dev` (0.5c): services are created without a
// source and deployed with `railway up <app dir>`. A follow-up PR flips this to true:
// then every push to `dev` deploys, only after CI (`checkSuites`) has passed.
const GITHUB_AUTODEPLOY = false;

// Per Railway environment: the Supabase project and the API's APP_ENV (apps/api
// AppEnv: dev | prod; Railway's `production` is APP_ENV=prod). Only `dev` exists until
// pilot gate P1.
const ENVIRONMENTS: Record<string, { supabaseUrl: string; appEnv: "dev" | "prod" }> = {
  dev: { supabaseUrl: "https://ynscpsuhflxyklsfhdei.supabase.co", appEnv: "dev" },
};

// Same region as Supabase (us-east-1) to keep DB round trips short.
const REGION = "us-east4-eqdc4a";

export default defineRailway((ctx) => {
  const envName = ctx.environment ?? ctx.environmentName ?? "";
  const target = Object.hasOwn(ENVIRONMENTS, envName) ? ENVIRONMENTS[envName] : undefined;
  if (!target) {
    // Fail closed: `production` is brought up deliberately at P1, not by accident.
    throw new Error(`No configuration for Railway environment "${envName}" (only: dev).`);
  }
  const { supabaseUrl, appEnv } = target;

  const source = (rootDirectory: string, watch: string[]) =>
    GITHUB_AUTODEPLOY
      ? {
          source: github(REPO, { branch: envName, rootDirectory, checkSuites: true }),
          build: { builder: "DOCKERFILE" as const, watchPatterns: watch },
        }
      : { rootDirectory, build: { builder: "DOCKERFILE" as const } };

  const apiWatch = ["/apps/api/app/**", "/apps/api/pyproject.toml", "/apps/api/uv.lock",
    "/apps/api/Dockerfile", "/apps/api/.dockerignore"];

  // Railway resolves ${{service.VAR}} references when it starts a deployment.
  const webOrigin = "https://${{web.RAILWAY_PUBLIC_DOMAIN}}";
  const apiUrl = "https://${{api.RAILWAY_PUBLIC_DOMAIN}}";

  const api = service("api", {
    ...source("apps/api", apiWatch),
    healthcheck: "/health",
    healthcheckTimeout: 60,
    replicas: { [REGION]: 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5, drainingSeconds: 15 },
    env: {
      APP_ENV: appEnv,
      LOG_LEVEL: "INFO",
      // po_api through the Supavisor session pooler (TLS). Set by the owner only.
      DATABASE_URL: preserve(),
      // Supabase CA (PEM, public): the API verifies the DB certificate + hostname.
      DATABASE_CA_CERT: preserve(),
      SUPABASE_URL: supabaseUrl,
      WEB_ORIGIN: webOrigin,
    },
  });

  const worker = service("worker", {
    ...source("apps/api", apiWatch),
    start: "python -m app.worker",
    replicas: { [REGION]: 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5, drainingSeconds: 15 },
    env: {
      APP_ENV: appEnv,
      LOG_LEVEL: "INFO",
      // Same po_api role as the API until M1 introduces the worker's own role.
      DATABASE_URL: api.env.DATABASE_URL,
      DATABASE_CA_CERT: api.env.DATABASE_CA_CERT,
      SUPABASE_URL: supabaseUrl,
      WEB_ORIGIN: webOrigin,
    },
  });

  const web = service("web", {
    ...source("apps/web", ["/apps/web/src/**", "/apps/web/package.json",
      "/apps/web/pnpm-lock.yaml", "/apps/web/pnpm-workspace.yaml", "/apps/web/next.config.ts",
      "/apps/web/tsconfig.json", "/apps/web/Dockerfile", "/apps/web/.dockerignore",
      "/apps/web/public/**", "/apps/web/postcss.config.*", "/apps/web/components.json"]),
    healthcheck: "/healthz",
    healthcheckTimeout: 60,
    replicas: { [REGION]: 1 },
    deploy: { restartPolicyType: "ON_FAILURE", restartPolicyMaxRetries: 5, drainingSeconds: 15 },
    // Public values only (inlined into the browser bundle at build time).
    env: {
      NEXT_PUBLIC_API_URL: apiUrl,
      NEXT_PUBLIC_SUPABASE_URL: supabaseUrl,
      // sb_publishable_... of the same Supabase project; set by the owner.
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: preserve(),
    },
  });

  return project("po-agent", { resources: [api, worker, web] });
});
