// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ headers: async () => new Headers() }));

const { AUTH_RATE_RULES, emailKey, requestIpKey } = await import("./rate-limits");

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
});

describe("requestIpKey", () => {
  it("falls back to one shared bucket without a trusted IP", async () => {
    expect(await requestIpKey()).toBe("unknown");
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
      loginEmailFailures: [5, 15],
      confirmIp: [10, 5],
      resetIp: [5, 15],
      resetEmail: [3, 60],
    });
  });
});
