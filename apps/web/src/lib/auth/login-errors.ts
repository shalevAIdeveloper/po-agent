/** Maps a Supabase sign-in failure to one message. Never reveals whether the email exists. */
export function loginErrorMessage(error: { status?: number; code?: string } | null): string {
  if (error?.status === 429 || error?.code === "over_request_rate_limit") {
    return "Too many sign-in attempts. Wait a few minutes and try again.";
  }
  // No status / 0: network failure (AuthRetryableFetchError); 5xx: Auth is down.
  if (!error?.status || error.status >= 500) {
    return "Sign-in is temporarily unavailable. Try again in a moment.";
  }
  // invalid_credentials, email_not_confirmed, user_banned…: one answer for all.
  return "The email or password is incorrect.";
}
