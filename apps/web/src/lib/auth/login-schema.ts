import { z } from "zod";

import type { FormState } from "@/lib/forms/form-state";

/**
 * Login only checks presence and sane bounds. The 12-character policy applies when a
 * password is set (0.4c-2), not here: an older password must still be able to sign in.
 */
export const loginSchema = z.object({
  email: z.email({ error: "Enter a valid email address." }).max(320),
  password: z.string().min(1, { error: "Enter your password." }).max(1024),
  // No length limit here: an oversized `next` must not fail the form silently;
  // safeNextPath() drops it (falls back to /app) instead.
  next: z.string().optional(),
});

export type LoginField = "email" | "password";
export type LoginFieldErrors = Partial<Record<LoginField, string>>;

/** `values` only ever holds the email, never the password. */
export type LoginState = FormState<LoginField>;

export function parseLoginForm(formData: FormData) {
  const raw = {
    email: String(formData.get("email") ?? "").trim(),
    password: String(formData.get("password") ?? ""),
    next: formData.get("next") === null ? undefined : String(formData.get("next")),
  };
  const result = loginSchema.safeParse(raw);
  if (result.success) {
    return { ok: true as const, data: result.data };
  }
  const fieldErrors: LoginFieldErrors = {};
  for (const issue of result.error.issues) {
    const field = issue.path[0];
    if ((field === "email" || field === "password") && fieldErrors[field] === undefined) {
      fieldErrors[field] = issue.message;
    }
  }
  return { ok: false as const, fieldErrors, email: raw.email };
}
