import { describe, expect, it } from "vitest";

import { decideRoute, isProtectedPath } from "@/lib/auth/routes";

describe("isProtectedPath", () => {
  it.each(["/app", "/app/", "/app/orders", "/app/orders/123"])("protects %s", (path) => {
    expect(isProtectedPath(path)).toBe(true);
  });

  it.each(["/", "/login", "/apple", "/application", "/health"])("leaves %s public", (path) => {
    expect(isProtectedPath(path)).toBe(false);
  });
});

describe("decideRoute", () => {
  it("sends a guest on a protected path to login, keeping where they were going", () => {
    expect(decideRoute("/app/orders", false)).toEqual({
      action: "redirect",
      to: "/login",
      withNext: true,
    });
  });

  it("lets a signed-in user through to protected paths", () => {
    expect(decideRoute("/app", true)).toEqual({ action: "next" });
  });

  it("sends a signed-in user away from login", () => {
    expect(decideRoute("/login", true)).toEqual({ action: "redirect", to: "/app", withNext: false });
  });

  it("does not redirect a signed-in POST to /login (a Server Action would be replayed)", () => {
    expect(decideRoute("/login", true, "POST")).toEqual({ action: "next" });
    expect(decideRoute("/login", true, "HEAD")).toMatchObject({ action: "redirect" });
  });

  it("still sends a guest POST to a protected path to login", () => {
    expect(decideRoute("/app", false, "POST")).toMatchObject({ action: "redirect", to: "/login" });
  });

  it.each([
    ["/", false],
    ["/", true],
    ["/login", false],
  ] as const)("does nothing for %s (signed in: %s)", (path, signedIn) => {
    expect(decideRoute(path, signedIn)).toEqual({ action: "next" });
  });
});
