"use server";

import { redirect } from "next/navigation";

import { parseEmailLink } from "@/lib/auth/email-link";
import { allowEmailLinkCheck, requestIp } from "@/lib/auth/rate-limits";
import { type FormState, formString, nextAttempt } from "@/lib/forms/form-state";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type ConfirmState = FormState<never> & {
  expired?: boolean;
  /**
   * Refused by our own limit (ADR 0009), not by Auth. Same message as Auth's 429; only
   * exposed as a data attribute so the deployed check (`pnpm test:deployed`) can tell
   * the two apart. Says nothing about any account.
   */
  limited?: boolean;
};

const TOO_MANY_ATTEMPTS = "Too many attempts. Wait a few minutes and try again.";

/**
 * Verifies the email link only on an explicit POST (the "Continue" button): email
 * security scanners that open links with GET must not use up the one-time token.
 */
export async function confirmEmailLink(
  previous: ConfirmState,
  formData: FormData,
): Promise<ConfirmState> {
  const attempt = nextAttempt(previous);
  const link = parseEmailLink(formString(formData, "type"), formString(formData, "token_hash"));
  if (!link) {
    return { attempt, expired: true, error: "This link is not valid." };
  }
  // Per client IP (A13): Auth's `token_verifications` limit only sees this server's IP.
  if (!allowEmailLinkCheck(await requestIp())) {
    return { attempt, error: TOO_MANY_ATTEMPTS, limited: true };
  }

  const supabase = await createSupabaseServerClient();
  // Someone else signed in in this browser: end that session first, so a link for a
  // different account never silently swaps it (sec M, 0.4c-2). /set-password then
  // shows which account the link belongs to.
  try {
    const { data } = await supabase.auth.getClaims();
    if (data?.claims.sub) {
      await supabase.auth.signOut({ scope: "local" });
    }
  } catch {
    // No usable session: nothing to sign out.
  }

  const { error } = await supabase.auth.verifyOtp({ type: link.type, token_hash: link.tokenHash });
  if (error) {
    // Expired, already used, or a different link was opened after this one.
    if (error.status === 429) {
      return { attempt, error: TOO_MANY_ATTEMPTS };
    }
    if (!error.status || error.status >= 500) {
      return { attempt, error: "We couldn't check your link right now. Try again in a moment." };
    }
    return { attempt, expired: true, error: "This link has expired or has already been used." };
  }

  // A session now exists (cookies set by the server client): choose a password.
  redirect("/set-password");
}
