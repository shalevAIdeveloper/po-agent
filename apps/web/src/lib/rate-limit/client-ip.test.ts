// @vitest-environment node
import { describe, expect, it } from "vitest";

import { clientIpKey, parseClientIp } from "./client-ip";

function parse(values: Record<string, string>) {
  return parseClientIp(new Headers(values));
}

function key(values: Record<string, string>): string | null {
  return clientIpKey(new Headers(values));
}

describe("parseClientIp", () => {
  it("uses X-Real-IP (set by Railway's edge)", () => {
    expect(parse({ "x-real-ip": "203.0.113.7" })).toEqual({ key: "203.0.113.7", wide: null });
  });

  it("trims (a plain object, not Headers, which trims by itself)", () => {
    expect(parseClientIp({ get: () => "  203.0.113.7 " })).toEqual({ key: "203.0.113.7", wide: null });
  });

  it("never reads X-Forwarded-For, whatever it says", () => {
    expect(parse({ "x-forwarded-for": "198.51.100.1" })).toEqual({ reason: "missing" });
    expect(key({ "x-real-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.1, 203.0.113.7" })).toBe(
      "203.0.113.7",
    );
  });

  it.each([
    ["missing", {}, "missing"],
    ["empty", { "x-real-ip": "" }, "missing"],
    ["two values (a duplicated header is comma-joined)", { "x-real-ip": "203.0.113.7, 198.51.100.1" }, "malformed"],
    ["a hostname", { "x-real-ip": "example.com" }, "malformed"],
    ["an out-of-range octet", { "x-real-ip": "256.1.1.1" }, "malformed"],
    ["a port", { "x-real-ip": "203.0.113.7:443" }, "malformed"],
    ["a bracketed IPv6", { "x-real-ip": "[2001:db8::1]" }, "malformed"],
    ["a CIDR", { "x-real-ip": "203.0.113.0/24" }, "malformed"],
    ["junk", { "x-real-ip": "unknown" }, "malformed"],
  ])("gives a reason, no key, for %s", (_name, values, reason) => {
    expect(parse(values)).toEqual({ reason });
    expect(key(values)).toBeNull();
  });

  it("keys IPv6 by its /64 and /48, in one canonical form", () => {
    expect(parse({ "x-real-ip": "2001:db8:a:b:1:2:3:4" })).toEqual({
      key: "2001:db8:a:b::/64",
      wide: "2001:db8:a::/48",
    });
    expect(key({ "x-real-ip": "2001:0DB8:000A:000B:ffff::1" })).toBe("2001:db8:a:b::/64");
    expect(key({ "x-real-ip": "2001:db8::1" })).toBe("2001:db8:0:0::/64");
    expect(key({ "x-real-ip": "::1" })).toBe("0:0:0:0::/64");
    expect(key({ "x-real-ip": "2001:db8:a:b::" })).toBe("2001:db8:a:b::/64");
    expect(key({ "x-real-ip": "64:ff9b::192.0.2.33" })).toBe("64:ff9b:0:0::/64");
    // A dotted tail is two groups, which shifts where "::" expands.
    expect(key({ "x-real-ip": "1::2:3:4:5:6.7.8.9" })).toBe("1:0:2:3::/64");
    expect(key({ "x-real-ip": "fe80::1%eth0" })).toBe("fe80:0:0:0::/64");
  });

  it("puts every address of one /64 in the same bucket and other /64s in others", () => {
    const a = key({ "x-real-ip": "2001:db8:a:b::1" });
    expect(key({ "x-real-ip": "2001:db8:a:b:ffff:ffff:ffff:ffff" })).toBe(a);
    expect(key({ "x-real-ip": "2001:db8:a:c::1" })).not.toBe(a);
  });

  it("gives every /64 of one /48 the same wide key", () => {
    const wide = (ip: string) => (parse({ "x-real-ip": ip }) as { wide: string }).wide;
    expect(wide("2001:db8:a:1::1")).toBe(wide("2001:db8:a:ffff::1"));
    expect(wide("2001:db8:b:1::1")).not.toBe(wide("2001:db8:a:1::1"));
  });

  it.each([
    "::ffff:203.0.113.7",
    "::FFFF:203.0.113.7",
    "0:0:0:0:0:ffff:203.0.113.7",
    "::ffff:cb00:7107",
    "::ffff:0:203.0.113.7",
    "0:0:0:0:ffff:0:cb00:7107",
  ])("keys IPv4 embedded in IPv6 (%s) as the IPv4 address", (ip) => {
    expect(parse({ "x-real-ip": ip })).toEqual({ key: "203.0.113.7", wide: null });
  });

  it("does not treat other ::ffff-looking addresses as IPv4", () => {
    expect(key({ "x-real-ip": "1::ffff:203.0.113.7" })).toBe("1:0:0:0::/64");
  });
});
