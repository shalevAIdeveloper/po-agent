"use server";

import { redirect } from "next/navigation";

import { isCredentialRejection, loginErrorMessage } from "@/lib/auth/login-errors";
import { type LoginState, parseLoginForm } from "@/lib/auth/login-schema";
import {
  allowLogin,
  recordLoginSuccess,
  releaseLoginAttempt,
  requestIp,
} from "@/lib/auth/rate-limits";
import { safeNextPath } from "@/lib/auth/next-path";
import { nextAttempt } from "@/lib/forms/form-state";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function login(previous: LoginState, formData: FormData): Promise<LoginState> {
  const attempt = nextAttempt(previous);
  const parsed = parseLoginForm(formData);
  if (!parsed.ok) {
    return { attempt, fieldErrors: parsed.fieldErrors, values: { email: parsed.email } };
  }

  const { email, password, next } = parsed.data;
  // Our limits per client IP and per account (A13): Auth's own per-IP limit only sees
  // this server's IP. Same message as Auth's 429, so a locked account looks like any
  // other rate limit.
  const allowed = allowLogin(await requestIp(), email);
  if (!allowed) {
    return { attempt, error: loginErrorMessage({ status: 429 }), values: { email } };
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    // The attempt was reserved against the account; only a rejected email/password
    // keeps it (not Auth outages or Auth's own rate limit). Nothing about the attempt
    // is logged: the email is PII.
    if (!isCredentialRejection(error)) releaseLoginAttempt(allowed);
    return { attempt, error: loginErrorMessage(error), values: { email } };
  }
  recordLoginSuccess(allowed);

  // Session cookies were written by the server client; `next` is re-validated here
  // because the hidden field is user-controlled.
  redirect(safeNextPath(next));
}
