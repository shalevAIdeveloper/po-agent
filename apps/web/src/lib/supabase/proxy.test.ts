// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

type CookieToSet = { name: string; value: string; options: Record<string, unknown> };
type SetAll = (cookies: CookieToSet[], headers: Record<string, string>) => void;

const state = vi.hoisted(() => ({
  claims: null as { sub: string } | null,
  refreshed: [] as CookieToSet[],
}));

// The exact headers @supabase/ssr 0.12 passes to setAll() with every cookie write.
const SSR_NO_CACHE_HEADERS = vi.hoisted(() => ({
  "Cache-Control": "private, no-cache, no-store, must-revalidate, max-age=0",
  Expires: "0",
  Pragma: "no-cache",
}));

vi.mock("@supabase/ssr", () => ({
  createServerClient: (_url: string, _key: string, options: { cookies: { setAll: SetAll } }) => ({
    auth: {
      getClaims: async () => {
        if (state.refreshed.length > 0) {
          options.cookies.setAll(state.refreshed, SSR_NO_CACHE_HEADERS);
        }
        return { data: state.claims ? { claims: state.claims } : null, error: null };
      },
    },
  }),
}));

const { updateSession } = await import("@/lib/supabase/proxy");

function request(path: string): NextRequest {
  return new NextRequest(new URL(path, "http://localhost:3100"));
}

beforeEach(() => {
  state.claims = null;
  state.refreshed = [];
});

describe("updateSession", () => {
  it("redirects a guest from /app to /login, keeping path and query in `next`", async () => {
    const response = await updateSession(request("/app/orders?status=new"));

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/app/orders?status=new");
  });

  it("lets a signed-in user through and passes refreshed cookies on", async () => {
    state.claims = { sub: "user-1" };
    state.refreshed = [{ name: "sb-auth-token", value: "new", options: { httpOnly: false } }];

    const response = await updateSession(request("/app"));

    expect(response.headers.get("location")).toBeNull();
    expect(response.cookies.get("sb-auth-token")?.value).toBe("new");
    expect(response.headers.get("cache-control")).toBe(SSR_NO_CACHE_HEADERS["Cache-Control"]);
  });

  it("forwards refreshed cookies to the request that Server Components read", async () => {
    state.claims = { sub: "user-1" };
    state.refreshed = [{ name: "sb-auth-token", value: "new", options: {} }];

    const response = await updateSession(request("/app"));

    // NextResponse.next({ request }) carries the rewritten request cookies in this header.
    expect(response.headers.get("x-middleware-request-cookie")).toContain("sb-auth-token=new");
  });

  it("keeps refreshed cookies, removals and all no-cache headers on a redirect", async () => {
    state.claims = { sub: "user-1" };
    state.refreshed = [
      // A stale chunk being cleared, as @supabase/ssr does when the token shrinks.
      { name: "sb-auth-token.1", value: "", options: { path: "/", maxAge: 0 } },
      { name: "sb-auth-token", value: "new", options: { path: "/", httpOnly: false } },
    ];

    const response = await updateSession(request("/login"));

    expect(new URL(response.headers.get("location") ?? "").pathname).toBe("/app");
    expect(response.cookies.get("sb-auth-token")?.value).toBe("new");
    expect(response.cookies.get("sb-auth-token.1")).toMatchObject({ value: "", maxAge: 0 });
    for (const [key, value] of Object.entries(SSR_NO_CACHE_HEADERS)) {
      expect(response.headers.get(key)).toBe(value);
    }
  });

  it("leaves public pages alone for guests", async () => {
    const response = await updateSession(request("/"));

    expect(response.headers.get("location")).toBeNull();
  });
});
