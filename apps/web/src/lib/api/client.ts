import { z } from "zod";

import { env } from "@/lib/env";

// Hand-written until 0.4b generates types from the API's OpenAPI schema.
const healthSchema = z.object({
  status: z.literal("ok"),
  env: z.enum(["local", "dev", "prod"]),
  version: z.string(),
});

export type Health = z.infer<typeof healthSchema>;

export type ApiErrorKind = "network" | "http" | "invalid_response";

/** Never carries response bodies: they may contain customer data. */
export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly status?: number,
  ) {
    super(status === undefined ? `API ${kind} error` : `API ${kind} error (${status})`);
    this.name = "ApiError";
  }
}

const DEFAULT_TIMEOUT_MS = 5_000;

type Fetch = typeof fetch;

/** Joins without dropping a base path: ("https://h/api", "/health") → https://h/api/health. */
export function resolveApiUrl(baseUrl: string, path: string): URL {
  const base = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return new URL(path.replace(/^\/+/, ""), base);
}

export function createApiClient(baseUrl: string, fetchImpl: Fetch = fetch) {
  async function request<T>(path: string, schema: z.ZodType<T>): Promise<T> {
    let response: Response;
    try {
      response = await fetchImpl(resolveApiUrl(baseUrl, path), {
        headers: { accept: "application/json" },
        cache: "no-store",
        signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
      });
    } catch {
      throw new ApiError("network");
    }

    if (!response.ok) {
      throw new ApiError("http", response.status);
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ApiError("invalid_response", response.status);
    }

    const parsed = schema.safeParse(body);
    if (!parsed.success) {
      throw new ApiError("invalid_response", response.status);
    }
    return parsed.data;
  }

  return {
    getHealth: () => request("/health", healthSchema),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api: ApiClient = createApiClient(env.NEXT_PUBLIC_API_URL);
