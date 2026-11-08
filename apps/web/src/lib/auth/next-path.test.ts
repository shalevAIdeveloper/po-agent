import { describe, expect, it } from "vitest";

import { DEFAULT_AFTER_LOGIN, safeNextPath } from "@/lib/auth/next-path";

describe("safeNextPath", () => {
  it.each([
    ["/app", "/app"],
    ["/app/orders?status=new", "/app/orders?status=new"],
    ["/app#section", "/app#section"],
    ["/app/../app/x", "/app/x"],
  ])("keeps internal path %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    undefined,
    null,
    42,
    ["/app"],
    "",
    "app",
    "//evil.com",
    "//evil.com/app",
    "/\\evil.com",
    "\\\\evil.com",
    "/app\\..\\..\\evil",
    "https://evil.com",
    "http:/evil.com",
    "javascript:alert(1)",
    "/\tevil.com",
    "/\n/evil.com",
    "/\u0000",
    "/login",
    "/login?next=/app",
    `/${"a".repeat(2048)}`,
    // Dot segments that normalize to a protocol-relative path (`//evil.com`).
    "/.//evil.com",
    "/a/..//evil.com",
    "/%2e//evil.com",
    "/%2E%2E//evil.com",
    "/app/..//evil.com/x",
  ])("falls back to the default for %j", (input) => {
    expect(safeNextPath(input)).toBe(DEFAULT_AFTER_LOGIN);
  });

  // Encoded / unicode lookalikes stay percent-encoded paths on our origin (sec delta).
  it.each(["/%5cevil.com", "/%2f%2fevil.com", "/%2F/evil.com", "/％2f／evil.com", "/@evil.com", "/ /evil.com"])(
    "keeps %j same-origin, never protocol-relative",
    (input) => {
      const result = safeNextPath(input);
      expect(result.startsWith("/")).toBe(true);
      expect(result.startsWith("//")).toBe(false);
      expect(new URL(result, "http://app.test").origin).toBe("http://app.test");
    },
  );

  // The login page sanitizes `next` into a hidden field and the action sanitizes it
  // again: the second pass must not change (or unlock) anything the first returned.
  it.each(["/app", "/app/orders?status=new", "/.//evil.com", "/app/../app/x", "/%2e%2e/app"])(
    "is idempotent for %j",
    (input) => {
      const once = safeNextPath(input);
      expect(safeNextPath(once)).toBe(once);
      expect(once.startsWith("//")).toBe(false);
    },
  );
});
