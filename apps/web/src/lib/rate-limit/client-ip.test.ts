// @vitest-environment node
import { describe, expect, it } from "vitest";

import { clientIpKey } from "./client-ip";

function key(values: Record<string, string>): string | null {
  return clientIpKey(new Headers(values));
}

describe("clientIpKey", () => {
  it("uses X-Real-IP (set by Railway's edge)", () => {
    expect(key({ "x-real-ip": "203.0.113.7" })).toBe("203.0.113.7");
    expect(key({ "x-real-ip": "  203.0.113.7 " })).toBe("203.0.113.7");
  });

  it("never reads X-Forwarded-For, whatever it says", () => {
    expect(key({ "x-forwarded-for": "198.51.100.1" })).toBeNull();
    expect(key({ "x-real-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.1, 203.0.113.7" })).toBe(
      "203.0.113.7",
    );
  });

  it.each([
    ["missing", {}],
    ["empty", { "x-real-ip": "" }],
    ["two values (a duplicated header is comma-joined)", { "x-real-ip": "203.0.113.7, 198.51.100.1" }],
    ["a hostname", { "x-real-ip": "example.com" }],
    ["an out-of-range octet", { "x-real-ip": "256.1.1.1" }],
    ["a port", { "x-real-ip": "203.0.113.7:443" }],
    ["a bracketed IPv6", { "x-real-ip": "[2001:db8::1]" }],
    ["a CIDR", { "x-real-ip": "203.0.113.0/24" }],
    ["junk", { "x-real-ip": "unknown" }],
  ])("gives null for %s", (_name, values) => {
    expect(key(values)).toBeNull();
  });

  it("reduces IPv6 to its /64, in one canonical form", () => {
    expect(key({ "x-real-ip": "2001:db8:a:b:1:2:3:4" })).toBe("2001:db8:a:b::/64");
    expect(key({ "x-real-ip": "2001:0DB8:000A:000B:ffff::1" })).toBe("2001:db8:a:b::/64");
    expect(key({ "x-real-ip": "2001:db8::1" })).toBe("2001:db8:0:0::/64");
    expect(key({ "x-real-ip": "::1" })).toBe("0:0:0:0::/64");
    expect(key({ "x-real-ip": "2001:db8:a:b::" })).toBe("2001:db8:a:b::/64");
    expect(key({ "x-real-ip": "64:ff9b::192.0.2.33" })).toBe("64:ff9b:0:0::/64");
  });

  it("puts every address of one /64 in the same bucket and other /64s in others", () => {
    const a = key({ "x-real-ip": "2001:db8:a:b::1" });
    expect(key({ "x-real-ip": "2001:db8:a:b:ffff:ffff:ffff:ffff" })).toBe(a);
    expect(key({ "x-real-ip": "2001:db8:a:c::1" })).not.toBe(a);
  });

  it("treats an IPv4-mapped IPv6 address as the IPv4 address", () => {
    expect(key({ "x-real-ip": "::ffff:203.0.113.7" })).toBe("203.0.113.7");
    expect(key({ "x-real-ip": "::FFFF:203.0.113.7" })).toBe("203.0.113.7");
  });
});
