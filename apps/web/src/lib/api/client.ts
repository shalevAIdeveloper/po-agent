import { z } from "zod";

import type { components } from "@/lib/api/schema";
import { env } from "@/lib/env";

type Schemas = components["schemas"];

// Types come from the API's OpenAPI schema (`pnpm gen:api`, ADR 0006). The Zod
// schemas validate responses at runtime; `satisfies` makes them fail to compile
// if they drift from the generated types.
const healthSchema = z.object({
  status: z.literal("ok"),
  env: z.enum(["local", "dev", "prod"]),
  version: z.string(),
}) satisfies z.ZodType<Schemas["HealthResponse"]>;

const organizationSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  slug: z.string(),
  role: z.enum(["owner", "admin", "reviewer"]),
}) satisfies z.ZodType<Schemas["Organization"]>;

const meSchema = z.object({
  user_id: z.uuid(),
  organizations: z.array(organizationSchema),
}) satisfies z.ZodType<Schemas["MeResponse"]>;

// The API's documented error body (A5). Only `detail` strings that look like a
// stable code are kept; free text is dropped so it can never reach the UI or logs.
const errorBodySchema = z.object({
  detail: z.string(),
}) satisfies z.ZodType<Schemas["ErrorResponse"]>;
const ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;

export type Health = Schemas["HealthResponse"];
export type Organization = Schemas["Organization"];
export type Me = Schemas["MeResponse"];

export type ApiErrorKind = "network" | "http" | "invalid_response";

type ApiErrorDetails = {
  status?: number;
  /** Stable machine code from the API (e.g. `busy`, `slug_unavailable`), if any. */
  code?: string;
  /** From `Retry-After` on 503 `busy`, in seconds. */
  retryAfterSeconds?: number;
};

/** Never carries response bodies: they may contain customer data. */
export class ApiError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly retryAfterSeconds?: number;

  constructor(
    readonly kind: ApiErrorKind,
    details: ApiErrorDetails = {},
  ) {
    const { status } = details;
    super(status === undefined ? `API ${kind} error` : `API ${kind} error (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = details.code;
    this.retryAfterSeconds = details.retryAfterSeconds;
  }
}

const DEFAULT_TIMEOUT_MS = 5_000;
const MAX_RETRY_AFTER_SECONDS = 300;

type Fetch = typeof fetch;

/** Joins without dropping a base path: ("https://h/api", "/health") → https://h/api/health. */
export function resolveApiUrl(baseUrl: string, path: string): URL {
  const base = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return new URL(path.replace(/^\/+/, ""), base);
}

/** Retry-After as delta-seconds only (the API never sends an HTTP date). */
export function parseRetryAfter(value: string | null): number | undefined {
  if (value === null || !/^\d{1,6}$/.test(value.trim())) {
    return undefined;
  }
  return Math.min(Number(value.trim()), MAX_RETRY_AFTER_SECONDS);
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let code: string | undefined;
  try {
    const parsed = errorBodySchema.safeParse(await response.json());
    if (parsed.success && ERROR_CODE.test(parsed.data.detail)) {
      code = parsed.data.detail;
    }
  } catch {
    // Non-JSON error body (e.g. a proxy page): status alone is enough.
  }
  return new ApiError("http", {
    status: response.status,
    code,
    retryAfterSeconds: parseRetryAfter(response.headers.get("retry-after")),
  });
}

type RequestOptions = {
  /** Supabase access token; sent as a Bearer token, never logged. */
  accessToken?: string;
  method?: "GET" | "POST" | "PATCH";
  /** Sent as JSON. */
  body?: unknown;
};

export type OrganizationCreate = Schemas["OrganizationCreate"];

export function createApiClient(baseUrl: string, fetchImpl: Fetch = fetch) {
  async function request<T>(
    path: string,
    schema: z.ZodType<T>,
    { accessToken, method = "GET", body: requestBody }: RequestOptions = {},
  ): Promise<T> {
    const headers: Record<string, string> = { accept: "application/json" };
    if (accessToken !== undefined) {
      headers.authorization = `Bearer ${accessToken}`;
    }
    if (requestBody !== undefined) {
      headers["content-type"] = "application/json";
    }

    let response: Response;
    try {
      response = await fetchImpl(resolveApiUrl(baseUrl, path), {
        method,
        headers,
        body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
        cache: "no-store",
        signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
      });
    } catch {
      throw new ApiError("network");
    }

    if (!response.ok) {
      throw await errorFromResponse(response);
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ApiError("invalid_response", { status: response.status });
    }

    const parsed = schema.safeParse(body);
    if (!parsed.success) {
      throw new ApiError("invalid_response", { status: response.status });
    }
    return parsed.data;
  }

  return {
    getHealth: () => request("/health", healthSchema),
    getMe: (accessToken: string) => request("/me", meSchema, { accessToken }),
    // Not retried: a timeout after the API committed would otherwise create a second org.
    createOrganization: (accessToken: string, body: OrganizationCreate) =>
      request("/orgs", organizationSchema, { accessToken, method: "POST", body }),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api: ApiClient = createApiClient(env.NEXT_PUBLIC_API_URL);
