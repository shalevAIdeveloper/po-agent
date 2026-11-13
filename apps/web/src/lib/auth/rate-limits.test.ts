// @vitest-environment node
import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

const request = vi.hoisted(() => ({ headers: new Headers() }));
vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ headers: async () => request.headers }));

const { AUTH_RATE_RULES, allowEmailLinkCheck, allowLogin, allowPasswordReset, emailKey, logTag, requestIp } =
  await import("./rate-limits");

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

describe("rate_limit.refused log", () => {
  // Each test moves the clock on, so the 60-a-minute log budget starts fresh.
  let minute = 0;
  function nextMinute() {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(Date.UTC(2026, 10, 13, 12, (minute += 2))));
  }
  function events(spy: { mock: { calls: unknown[][] } }) {
    return spy.mock.calls.map(([line]) => JSON.parse(String(line)) as Record<string, unknown>);
  }

  it("an IP refusal logs the action, family and a tag of the bucket, never the IP", () => {
    nextMinute();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const ip = { key: "203.0.113.50", wide: null };

    for (let i = 0; i < 11; i++) allowEmailLinkCheck(ip);

    expect(events(info)).toEqual([
      { level: "info", event: "rate_limit.refused", action: "confirm", limit: "ip", family: "v4", key_tag: logTag(ip.key) },
    ]);
    expect(JSON.stringify(info.mock.calls)).not.toContain("203.0.113");
  });

  it("tags are stable per bucket, differ between buckets, and are not a plain hash", () => {
    expect(logTag("203.0.113.50")).toBe(logTag("203.0.113.50"));
    expect(logTag("203.0.113.50")).not.toBe(logTag("203.0.113.51"));
    expect(logTag("203.0.113.50")).toMatch(/^[0-9a-f]{12}$/);
    const plain = createHash("sha256").update("203.0.113.50").digest("hex").slice(0, 12);
    expect(logTag("203.0.113.50")).not.toBe(plain);
  });

  it("an IPv6 /48 refusal is tagged by the /48 and marked v6", () => {
    nextMinute();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    for (let i = 0; i < 101; i++) {
      allowEmailLinkCheck({ key: `2001:db8:77:${i.toString(16)}::/64`, wide: "2001:db8:77::/48" });
    }

    expect(events(info)).toEqual([
      expect.objectContaining({ action: "confirm", limit: "ip_wide", family: "v6", key_tag: logTag("2001:db8:77::/48") }),
    ]);
  });

  it("account and address refusals carry no tag (it would identify a person)", () => {
    nextMinute();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    for (let i = 0; i < 6; i++) allowLogin({ key: `198.51.100.${i}`, wide: null }, "locked@acme.test");
    for (let i = 0; i < 4; i++) allowPasswordReset({ key: `192.0.2.${i}`, wide: null }, "reset@acme.test");

    expect(events(info)).toEqual([
      { level: "info", event: "rate_limit.refused", action: "login", limit: "account" },
      { level: "info", event: "rate_limit.refused", action: "reset", limit: "address" },
    ]);
    expect(JSON.stringify(info.mock.calls)).not.toMatch(/acme|locked|reset@/);
  });

  it("the shared bucket without a trusted IP is logged as family unknown", () => {
    nextMinute();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    for (let i = 0; i < 11; i++) allowEmailLinkCheck({ key: "unknown", wide: null });

    expect(events(info)).toEqual([expect.objectContaining({ family: "unknown", key_tag: logTag("unknown") })]);
  });

  it("logs at most 60 refusals a minute and reports how many were dropped", () => {
    nextMinute();
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const ip = { key: "203.0.113.99", wide: null };
    for (let i = 0; i < 10; i++) allowEmailLinkCheck(ip);
    for (let i = 0; i < 75; i++) allowEmailLinkCheck(ip); // all refused

    expect(info).toHaveBeenCalledTimes(60);

    vi.setSystemTime(Date.now() + 60_000);
    allowEmailLinkCheck({ key: "203.0.113.98", wide: null }); // allowed, no log
    for (let i = 0; i < 10; i++) allowEmailLinkCheck({ key: "203.0.113.98", wide: null });

    expect(info).toHaveBeenCalledTimes(61);
    expect(events(info).at(-1)).toMatchObject({ dropped: 15 });
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
      loginIpWide: [100, 5],
      loginEmailFailures: [5, 15],
      confirmIp: [10, 5],
      confirmIpWide: [100, 5],
      resetIp: [5, 15],
      resetIpWide: [50, 15],
      resetEmail: [3, 60],
    });
  });
});
