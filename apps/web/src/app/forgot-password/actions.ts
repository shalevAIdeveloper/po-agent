"use server";

import { z } from "zod";

import { type FormState, formString, nextAttempt } from "@/lib/forms/form-state";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type ForgotPasswordState = FormState<"email">;

const emailSchema = z.email().max(320);

// Same answer whether or not the address has an account (no enumeration).
// (Not exported: a "use server" file may only export async functions.)
const RESET_SENT_NOTICE =
  "If an account exists for that email, we've sent a link to reset your password. It expires in 1 hour.";

export async function requestPasswordReset(
  previous: ForgotPasswordState,
  formData: FormData,
): Promise<ForgotPasswordState> {
  const attempt = nextAttempt(previous);
  const email = formString(formData, "email").trim();
  if (!emailSchema.safeParse(email).success) {
    return {
      attempt,
      fieldErrors: { email: "Enter a valid email address." },
      values: { email },
    };
  }

  const supabase = await createSupabaseServerClient();
  // No redirectTo: the recovery email template builds the /auth/confirm link itself.
  const { error } = await supabase.auth.resetPasswordForEmail(email);
  // Every 4xx, including rate limits, gets the same notice: Auth's per-address limit
  // (`max_frequency`) only fires for addresses that have an account (sec L, 0.4c-2).
  if (error && (!error.status || error.status >= 500)) {
    return {
      attempt,
      error: "We couldn't send the email right now. Try again in a moment.",
      values: { email },
    };
  }
  // Nothing about the request is logged: the email is PII.
  return { attempt, notice: RESET_SENT_NOTICE, values: { email } };
}
