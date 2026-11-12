// @vitest-environment node
import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

const request = vi.hoisted(() => ({ headers: new Headers() }));
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ headers: async () => request.headers }));

const { AUTH_RATE_RULES, emailKey, requestIp } = await import("./rate-limits");

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("emailKey", () => {
  it("is the same for one address however it is typed", () => {
    expect(emailKey(" Buyer@Acme.TEST ")).toBe(emailKey("buyer@acme.test"));
  });

  it("differs per address and never contains it", () => {
    const key = emailKey("buyer@acme.test");

    expect(key).not.toBe(emailKey("buyer2@acme.test"));
    expect(key).not.toContain("buyer");
    expect(key).toMatch(/^[A-Za-z0-9_-]{43}$/); // 32-byte HMAC, base64url
  });

  it("is keyed (not a plain hash anyone can recompute)", () => {
    const plain = createHash("sha256").update("buyer@acme.test").digest("base64url");
    expect(emailKey("buyer@acme.test")).not.toBe(plain);
  });
});

describe("requestIp", () => {
  it("returns the trusted client IP keys", async () => {
    request.headers = new Headers({ "x-real-ip": "2001:db8:a:b::1" });
    expect(await requestIp()).toEqual({ key: "2001:db8:a:b::/64", wide: "2001:db8:a::/48" });
  });

  it("without a trusted IP: one shared bucket, and a log event at most once a minute without the value", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-12T10:00:00Z"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    request.headers = new Headers({ "x-real-ip": "198.51.100.7, 203.0.113.9" });
    expect(await requestIp()).toEqual({ key: "unknown", wide: null });
    request.headers = new Headers();
    expect(await requestIp()).toEqual({ key: "unknown", wide: null });
    expect(warn).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date("2026-11-12T10:01:00Z"));
    await requestIp();
    expect(warn).toHaveBeenCalledTimes(2);

    const events = warn.mock.calls.map(([line]) => JSON.parse(String(line)) as Record<string, unknown>);
    expect(events).toEqual([
      { level: "warning", event: "rate_limit.ip_unknown", header: "malformed" },
      { level: "warning", event: "rate_limit.ip_unknown", header: "missing" },
    ]);
    expect(JSON.stringify(warn.mock.calls)).not.toContain("198.51.100.7");
  });
});

describe("AUTH_RATE_RULES", () => {
  it("are the limits recorded in ADR 0009", () => {
    const minutes = (ms: number) => ms / 60_000;
    const table = Object.fromEntries(
      Object.entries(AUTH_RATE_RULES).map(([name, rule]) => [name, [rule.limit, minutes(rule.windowMs)]]),
    );

    expect(table).toEqual({
      loginIp: [10, 5],
      loginIpWide: [30, 5],
      loginEmailFailures: [5, 15],
      confirmIp: [10, 5],
      confirmIpWide: [30, 5],
      resetIp: [5, 15],
      resetIpWide: [15, 15],
      resetEmail: [3, 60],
    });
  });
});
