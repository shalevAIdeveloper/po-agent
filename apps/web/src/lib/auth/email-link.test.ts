import { describe, expect, it } from "vitest";

import { parseEmailLink } from "@/lib/auth/email-link";

const HASH = "a3f1c2e4b5d6a7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c";

describe("parseEmailLink", () => {
  it.each(["invite", "recovery"])("accepts type %s", (type) => {
    expect(parseEmailLink(type, HASH)).toEqual({ type, tokenHash: HASH });
  });

  it("accepts a pkce_-prefixed hash", () => {
    expect(parseEmailLink("recovery", `pkce_${HASH}`)).not.toBeNull();
  });

  it.each([
    // Types we don't handle here must never reach verifyOtp.
    ["signup", HASH],
    ["magiclink", HASH],
    ["email_change", HASH],
    ["email", HASH],
    ["INVITE", HASH],
    ["invite", ""],
    ["invite", "short"],
    ["invite", `${HASH}'; drop`],
    ["invite", "a".repeat(257)],
    [undefined, HASH],
    ["invite", undefined],
    [["invite"], HASH],
    ["invite", [HASH]],
  ])("rejects (%j, %j)", (type, hash) => {
    expect(parseEmailLink(type, hash)).toBeNull();
  });
});
