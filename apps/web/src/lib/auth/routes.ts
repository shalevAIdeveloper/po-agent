/** Route rules applied by proxy.ts. Pure, so they can be unit-tested. */

export const LOGIN_PATH = "/login";

/** Paths that need a signed-in user. */
const PROTECTED_PREFIXES = ["/app", "/onboarding", "/set-password"] as const;
/** Pages a signed-in user should not see (sent to /app instead). */
const GUEST_ONLY_PATHS = [LOGIN_PATH] as const;

function matchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((prefix) => matchesPrefix(pathname, prefix));
}

export type RouteDecision =
  | { action: "next" }
  | { action: "redirect"; to: string; withNext: boolean };

export function decideRoute(pathname: string, signedIn: boolean, method = "GET"): RouteDecision {
  if (!signedIn && isProtectedPath(pathname)) {
    return { action: "redirect", to: LOGIN_PATH, withNext: true };
  }
  // GET/HEAD only: a 307 would replay a Server Action POST (e.g. a stale login tab)
  // against /app. Actions check the user themselves.
  const isRead = method === "GET" || method === "HEAD";
  if (signedIn && isRead && GUEST_ONLY_PATHS.some((path) => pathname === path)) {
    return { action: "redirect", to: "/app", withNext: false };
  }
  return { action: "next" };
}
