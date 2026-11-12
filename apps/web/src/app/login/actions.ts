"use server";

import { redirect } from "next/navigation";

import { loginErrorMessage } from "@/lib/auth/login-errors";
import { type LoginState, parseLoginForm } from "@/lib/auth/login-schema";
import {
  allowLogin,
  recordLoginFailure,
  recordLoginSuccess,
  requestIpKey,
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
  if (!allowLogin(await requestIpKey(), email)) {
    return { attempt, error: loginErrorMessage({ status: 429 }), values: { email } };
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    // Only a rejected email/password counts against the account (not Auth outages or
    // Auth's own 429). Nothing about the attempt is logged: the email is PII.
    if (error.status && error.status >= 400 && error.status < 500 && error.status !== 429) {
      recordLoginFailure(email);
    }
    return { attempt, error: loginErrorMessage(error), values: { email } };
  }
  recordLoginSuccess(email);

  // Session cookies were written by the server client; `next` is re-validated here
  // because the hidden field is user-controlled.
  redirect(safeNextPath(next));
}
