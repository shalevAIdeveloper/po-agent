import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, parseRetryAfter, resolveApiUrl } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";

const ME = {
  user_id: "11111111-1111-4111-8111-111111111111",
  organizations: [
    { id: "22222222-2222-4222-8222-222222222222", name: "Acme Supply", slug: "acme", role: "owner" },
  ],
};

describe("createApiClient().getMe", () => {
  it("sends the access token as a Bearer header and parses the body", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(ME), { status: 200 }),
    );

    const me = await createApiClient("http://api.test", fetchMock).getMe("token-abc");

    expect(me).toEqual(ME);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("http://api.test/me");
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer token-abc");
  });

  it("rejects an unknown role", async () => {
    const bad = { ...ME, organizations: [{ ...ME.organizations[0], role: "superuser" }] };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(bad)));

    await expect(createApiClient("http://api.test", fetchMock).getMe("t")).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("never puts the token in the error", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { status: 401 }));

    const error = await createApiClient("http://api.test", fetchMock)
      .getMe("secret-token-xyz")
      .catch((caught: unknown) => caught);

    expect(JSON.stringify(error)).not.toContain("secret-token-xyz");
    expect(String(error)).not.toContain("secret-token-xyz");
  });
});

describe("parseRetryAfter", () => {
  it.each([
    ["2", 2],
    [" 10 ", 10],
    ["0", 0],
    ["99999", 300],
    [null, undefined],
    ["", undefined],
    ["-1", undefined],
    ["1.5", undefined],
    ["Wed, 21 Oct 2026 07:28:00 GMT", undefined],
  ])("%j → %j", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });
});

describe("describeApiError", () => {
  it.each([
    [new ApiError("network"), "Can't reach PO Agent"],
    [new ApiError("invalid_response", { status: 200 }), "Unexpected response"],
    [new ApiError("http", { status: 503, code: "busy", retryAfterSeconds: 2 }), "The service is busy"],
    [new ApiError("http", { status: 503 }), "Sign-in is temporarily unavailable"],
    [new ApiError("http", { status: 401 }), "Your session has ended"],
    [new ApiError("http", { status: 403 }), "Not allowed"],
    [new ApiError("http", { status: 404 }), "Not found"],
    [new ApiError("http", { status: 500 }), "Something went wrong"],
    [new Error("boom"), "Something went wrong"],
  ])("%s → %s", (error, title) => {
    expect(describeApiError(error).title).toBe(title);
  });

  it("uses Retry-After in the busy message", () => {
    const busy = new ApiError("http", { status: 503, code: "busy", retryAfterSeconds: 2 });
    expect(describeApiError(busy).description).toBe("Try again in 2 seconds.");
    const one = new ApiError("http", { status: 503, code: "busy", retryAfterSeconds: 1 });
    expect(describeApiError(one).description).toBe("Try again in 1 second.");
  });
});

const BASE_URL = "http://api.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("resolveApiUrl", () => {
  it.each([
    ["http://api.test", "/health", "http://api.test/health"],
    ["http://api.test/", "/health", "http://api.test/health"],
    ["https://h.test/api", "/health", "https://h.test/api/health"],
    ["https://h.test/api/", "health", "https://h.test/api/health"],
  ])("%s + %s → %s", (base, path, expected) => {
    expect(resolveApiUrl(base, path).toString()).toBe(expected);
  });
});

describe("createApiClient().getHealth", () => {
  it("returns parsed health and calls /health", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ status: "ok", env: "local", version: "0.1.0" }),
    );

    const health = await createApiClient(BASE_URL, fetchMock).getHealth();

    expect(health).toEqual({ status: "ok", env: "local", version: "0.1.0" });
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toBe("http://api.test/health");
    expect(init?.cache).toBe("no-store");
    expect(new Headers(init?.headers).get("accept")).toBe("application/json");
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it("keeps a path prefix in the base URL", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ status: "ok", env: "dev", version: "0.1.0" }),
    );

    await createApiClient("http://api.test/v1", fetchMock).getHealth();

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("http://api.test/v1/health");
  });

  it("throws a network error when fetch rejects", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("fetch failed"));

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "network",
    });
  });

  it("treats a timeout as a network error", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new DOMException("The operation timed out.", "TimeoutError"));

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "network",
    });
  });

  it("throws an http error with the status, without the body", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "customer ACME 123 Main St" }, 503));

    const error = await createApiClient(BASE_URL, fetchMock)
      .getHealth()
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "http", status: 503 });
    expect(String(error)).not.toContain("ACME");
  });

  it("rejects a response with an unexpected shape", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "maybe" }));

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("keeps a code-like detail and Retry-After, never free text", async () => {
    const busy = new Response(JSON.stringify({ detail: "busy" }), {
      status: 503,
      headers: { "content-type": "application/json", "retry-after": "2" },
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(busy);

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "http",
      status: 503,
      code: "busy",
      retryAfterSeconds: 2,
    });
  });

  it.each([
    [{ detail: "Not authenticated" }],
    [{ detail: "customer ACME 123 Main St" }],
    [{ detail: [{ loc: ["body"], msg: "x" }] }],
    ["not json"],
  ])("drops a non-code error body %j", async (body) => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(typeof body === "string" ? body : JSON.stringify(body), { status: 400 }));

    const error = await createApiClient(BASE_URL, fetchMock)
      .getHealth()
      .catch((caught: unknown) => caught);

    expect(error).toMatchObject({ kind: "http", status: 400, code: undefined });
  });

  it("rejects a non-JSON response", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("<html>gateway</html>", { status: 200 }));

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});
