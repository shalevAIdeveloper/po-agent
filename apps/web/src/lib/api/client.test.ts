import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, resolveApiUrl } from "@/lib/api/client";

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

  it("rejects a non-JSON response", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("<html>gateway</html>", { status: 200 }));

    await expect(createApiClient(BASE_URL, fetchMock).getHealth()).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});
