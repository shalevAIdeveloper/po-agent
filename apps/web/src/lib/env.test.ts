import { describe, expect, it } from "vitest";

import { parsePublicEnv } from "@/lib/env";

describe("parsePublicEnv", () => {
  it("accepts an http(s) API URL", () => {
    expect(parsePublicEnv({ NEXT_PUBLIC_API_URL: "http://localhost:8000" })).toEqual({
      NEXT_PUBLIC_API_URL: "http://localhost:8000",
    });
  });

  it("rejects a missing API URL", () => {
    expect(() => parsePublicEnv({})).toThrow("Invalid public environment variables: NEXT_PUBLIC_API_URL");
  });

  it.each(["not a url", "ftp://example.com", "javascript:alert(1)"])("rejects %s", (value) => {
    expect(() => parsePublicEnv({ NEXT_PUBLIC_API_URL: value })).toThrow("NEXT_PUBLIC_API_URL");
  });
});
