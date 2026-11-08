import { describe, expect, it } from "vitest";

import { loginErrorMessage } from "@/lib/auth/login-errors";
import { parseLoginForm } from "@/lib/auth/login-schema";

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    data.set(key, value);
  }
  return data;
}

describe("parseLoginForm", () => {
  it("accepts an email and any non-empty password, trimming the email", () => {
    const result = parseLoginForm(form({ email: "  a@b.co ", password: "short", next: "/app" }));

    expect(result).toEqual({
      ok: true,
      data: { email: "a@b.co", password: "short", next: "/app" },
    });
  });

  it("reports one message per field and echoes the email, never the password", () => {
    const result = parseLoginForm(form({ email: "nope", password: "" }));

    expect(result).toEqual({
      ok: false,
      email: "nope",
      fieldErrors: {
        email: "Enter a valid email address.",
        password: "Enter your password.",
      },
    });
  });

  it("accepts an oversized `next` (sanitized later) instead of failing silently", () => {
    const result = parseLoginForm(form({ email: "a@b.co", password: "p", next: `/${"a".repeat(5000)}` }));

    expect(result.ok).toBe(true);
  });

  it("does not trim the password", () => {
    const result = parseLoginForm(form({ email: "a@b.co", password: " pass " }));

    expect(result.ok && result.data.password).toBe(" pass ");
  });
});

describe("loginErrorMessage", () => {
  it.each([
    [{ status: 400, code: "invalid_credentials" }],
    [{ status: 400, code: "email_not_confirmed" }],
    [{ status: 400, code: "user_banned" }],
    [{ status: 422 }],
  ])("gives one generic answer for %j", (error) => {
    expect(loginErrorMessage(error)).toBe("The email or password is incorrect.");
  });

  it("explains rate limiting", () => {
    expect(loginErrorMessage({ status: 429 })).toMatch(/Too many sign-in attempts/);
  });

  it.each([[{ status: 500 }], [{ status: 503 }], [{ status: 0 }], [{}], [null]])("treats %j as unavailable", (error) => {
    expect(loginErrorMessage(error)).toMatch(/temporarily unavailable/);
  });
});
