import { describe, expect, it } from "vitest";

import { parsePublicEnv } from "@/lib/env";

const VALID = {
  NEXT_PUBLIC_API_URL: "http://localhost:8000",
  NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:54321",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};

describe("parsePublicEnv", () => {
  it("accepts http(s) URLs and a publishable key", () => {
    expect(parsePublicEnv(VALID)).toEqual(VALID);
  });

  it("names every missing variable", () => {
    expect(() => parsePublicEnv({})).toThrow(
      "Invalid public environment variables: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    );
  });

  it.each(["not a url", "ftp://example.com", "javascript:alert(1)"])("rejects API URL %s", (value) => {
    expect(() => parsePublicEnv({ ...VALID, NEXT_PUBLIC_API_URL: value })).toThrow("NEXT_PUBLIC_API_URL");
  });

  it.each([
    "sb_secret_abc123",
    // Legacy service_role JWT shape (header.payload.signature).
    "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abc123",
    "anything-else",
  ])("rejects %s in the public key variable, without echoing it", (key) => {
    const parse = () => parsePublicEnv({ ...VALID, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: key });

    expect(parse).toThrow("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY");
    expect(parse).not.toThrow("abc123");
  });
});
