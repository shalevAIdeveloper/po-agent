import { describe, expect, it } from "vitest";

import { LINK_SIGN_IN_WINDOW_SECONDS, signedInByRecentEmailLink } from "@/lib/auth/amr";

const NOW = 1_800_000_000;

describe("signedInByRecentEmailLink", () => {
  it("accepts an otp sign-in (invite/recovery link) within 15 minutes", () => {
    expect(signedInByRecentEmailLink([{ method: "otp", timestamp: NOW - 60 }], NOW)).toBe(true);
    expect(
      signedInByRecentEmailLink([{ method: "otp", timestamp: NOW - LINK_SIGN_IN_WINDOW_SECONDS }], NOW),
    ).toBe(true);
  });

  it("rejects an older link sign-in", () => {
    expect(
      signedInByRecentEmailLink(
        [{ method: "otp", timestamp: NOW - LINK_SIGN_IN_WINDOW_SECONDS - 1 }],
        NOW,
      ),
    ).toBe(false);
  });

  it("rejects password sessions, even fresh ones", () => {
    expect(signedInByRecentEmailLink([{ method: "password", timestamp: NOW }], NOW)).toBe(false);
  });

  it("tolerates small clock skew but not a future timestamp", () => {
    expect(signedInByRecentEmailLink([{ method: "otp", timestamp: NOW + 30 }], NOW)).toBe(true);
    expect(signedInByRecentEmailLink([{ method: "otp", timestamp: NOW + 3600 }], NOW)).toBe(false);
  });

  it.each([undefined, null, "otp", {}, [], [{ method: "otp" }], [{ method: "otp", timestamp: "1" }]])(
    "rejects malformed amr %j",
    (amr) => {
      expect(signedInByRecentEmailLink(amr, NOW)).toBe(false);
    },
  );

  it("accepts when any entry is a recent link sign-in", () => {
    const amr = [
      { method: "password", timestamp: NOW - 86_400 },
      { method: "otp", timestamp: NOW - 10 },
    ];
    expect(signedInByRecentEmailLink(amr, NOW)).toBe(true);
  });
});
