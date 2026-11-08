"use server";

import { redirect } from "next/navigation";

import { loginErrorMessage } from "@/lib/auth/login-errors";
import { type LoginState, parseLoginForm } from "@/lib/auth/login-schema";
import { safeNextPath } from "@/lib/auth/next-path";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function login(previous: LoginState, formData: FormData): Promise<LoginState> {
  // `previous` round-trips through the client: never trust its shape.
  const attempt = (Number.isSafeInteger(previous?.attempt) ? previous.attempt : 0) + 1;
  const parsed = parseLoginForm(formData);
  if (!parsed.ok) {
    return { attempt, fieldErrors: parsed.fieldErrors, email: parsed.email };
  }

  const { email, password, next } = parsed.data;
  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    // Nothing about the attempt is logged: the email is PII.
    return { attempt, error: loginErrorMessage(error), email };
  }

  // Session cookies were written by the server client; `next` is re-validated here
  // because the hidden field is user-controlled.
  redirect(safeNextPath(next));
}
