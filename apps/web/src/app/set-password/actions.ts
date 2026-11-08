"use server";

import { redirect } from "next/navigation";

import { passwordUpdateErrorMessage, validateNewPassword } from "@/lib/auth/password";
import { requireUser } from "@/lib/auth/session";
import { type FormState, formString, nextAttempt } from "@/lib/forms/form-state";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type SetPasswordField = "password" | "confirm";
export type SetPasswordState = FormState<SetPasswordField>;

export async function setPassword(
  previous: SetPasswordState,
  formData: FormData,
): Promise<SetPasswordState> {
  const attempt = nextAttempt(previous);
  // The action authorizes itself (the proxy is not the boundary).
  const user = await requireUser();
  // Only right after an invite/recovery link: a stolen or unattended session must not
  // be able to replace the password without knowing the current one (sec M, 0.4c-2).
  if (!user.recentEmailLinkSignIn) {
    return {
      attempt,
      error: "For your security, open a new password link from your email to continue.",
    };
  }

  // Passwords are never trimmed, echoed back or logged.
  const password = formString(formData, "password");
  const confirm = formString(formData, "confirm");
  const fieldErrors = validateNewPassword(password, confirm);
  if (fieldErrors.password || fieldErrors.confirm) {
    return { attempt, fieldErrors };
  }

  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.updateUser({ password });
  if (error) {
    return { attempt, error: passwordUpdateErrorMessage(error) };
  }

  // /app sends users without an organization on to onboarding.
  redirect("/app");
}
