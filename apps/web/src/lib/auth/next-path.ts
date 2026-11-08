export const DEFAULT_AFTER_LOGIN = "/app";

/**
 * The `next` redirect target, only if it is a same-origin path; otherwise the default.
 * Blocks open redirects: `//evil.com`, `/\evil.com`, `https://…`, `javascript:…`,
 * and control characters that browsers strip before parsing.
 */
export function safeNextPath(next: unknown): string {
  if (typeof next !== "string" || next.length === 0 || next.length > 2048) {
    return DEFAULT_AFTER_LOGIN;
  }
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return DEFAULT_AFTER_LOGIN;
  }
  // Control characters (browsers strip tabs/newlines before parsing) and backslashes.
  if (/[\u0000-\u001f\u007f\\]/.test(next)) {
    return DEFAULT_AFTER_LOGIN;
  }
  // Final check: resolved against a dummy origin, it must stay on that origin.
  const base = "http://same-origin.invalid";
  let resolved: URL;
  try {
    resolved = new URL(next, base);
  } catch {
    return DEFAULT_AFTER_LOGIN;
  }
  // Dot segments can normalize into a protocol-relative path: "/.//evil.com" → "//evil.com".
  if (resolved.origin !== base || resolved.pathname.startsWith("//")) {
    return DEFAULT_AFTER_LOGIN;
  }
  // Never bounce back to the login page itself.
  if (resolved.pathname === "/login") {
    return DEFAULT_AFTER_LOGIN;
  }
  return `${resolved.pathname}${resolved.search}${resolved.hash}`;
}
