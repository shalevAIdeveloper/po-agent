import { describe, expect, it } from "vitest";

import {
  PASSWORD_MAX_BYTES,
  passwordBytes,
  passwordLength,
  passwordUpdateErrorMessage,
  validateNewPassword,
} from "@/lib/auth/password";

describe("validateNewPassword", () => {
  it("accepts exactly 12 characters with no composition rules", () => {
    expect(validateNewPassword("aaaaaaaaaaaa", "aaaaaaaaaaaa")).toEqual({});
  });

  it("rejects 11 characters", () => {
    expect(validateNewPassword("a".repeat(11), "a".repeat(11))).toEqual({
      password: "Use at least 12 characters.",
    });
  });

  it("counts characters, not UTF-16 units or bytes", () => {
    // 12 emoji = 12 characters (24 UTF-16 units, 48 bytes): allowed.
    const emoji = "🔒".repeat(12);
    expect(passwordLength(emoji)).toBe(12);
    expect(validateNewPassword(emoji, emoji)).toEqual({});
    // 11 Hebrew letters = 22 bytes but 11 characters: too short.
    const hebrew = "א".repeat(11);
    expect(validateNewPassword(hebrew, hebrew).password).toMatch(/at least 12/);
  });

  it("rejects passwords over 72 bytes (bcrypt would silently ignore the rest)", () => {
    const ok = "a".repeat(PASSWORD_MAX_BYTES);
    const tooLong = "a".repeat(PASSWORD_MAX_BYTES + 1);
    const hebrew = "א".repeat(37); // 74 bytes, 37 characters
    expect(validateNewPassword(ok, ok)).toEqual({});
    expect(validateNewPassword(tooLong, tooLong).password).toMatch(/too long/);
    expect(passwordBytes(hebrew)).toBe(74);
    expect(validateNewPassword(hebrew, hebrew).password).toMatch(/too long/);
  });

  it("reports a mismatch only once the password itself is acceptable", () => {
    expect(validateNewPassword("long-enough-pass", "long-enough-pasS")).toEqual({
      confirm: "The passwords don't match.",
    });
    expect(validateNewPassword("short", "other")).toEqual({
      password: "Use at least 12 characters.",
    });
  });

  it("does not trim (spaces are part of the password)", () => {
    expect(validateNewPassword("  eleven chr", "  eleven chr")).toEqual({});
  });
});

describe("passwordUpdateErrorMessage", () => {
  it.each([
    [{ status: 422, code: "weak_password", reasons: ["pwned"] }, /data breach/],
    [{ status: 422, code: "weak_password", reasons: ["length"] }, /at least 12/],
    [{ status: 422, code: "weak_password", reasons: ["characters"] }, /too weak/],
    [{ status: 422, code: "weak_password" }, /too weak/],
    [{ status: 422, code: "same_password" }, /different from your current/],
    [{ status: 400, code: "reauthentication_needed" }, /request a new link/],
    [{ status: 429 }, /Too many attempts/],
    [{ status: 401 }, /expired/],
    [{ status: 500 }, /couldn't save/],
    [{}, /couldn't save/],
  ])("%j → %s", (error, expected) => {
    expect(passwordUpdateErrorMessage(error)).toMatch(expected);
  });
});
