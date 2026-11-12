type AuthError = { status?: number; code?: string } | null;

/** Auth refused because of its own rate limit (not an answer about the credentials). */
export function isAuthRateLimited(error: AuthError): boolean {
  return error?.status === 429 || error?.code === "over_request_rate_limit";
}

/**
 * Auth judged the email/password (a 4xx that isn't its rate limit). Outages, network
 * failures and rate limits did not test the credentials.
 */
export function isCredentialRejection(error: AuthError): boolean {
  const status = error?.status;
  return Boolean(status && status >= 400 && status < 500) && !isAuthRateLimited(error);
}

/** Maps a Supabase sign-in failure to one message. Never reveals whether the email exists. */
export function loginErrorMessage(error: AuthError): string {
  if (isAuthRateLimited(error)) {
    return "Too many sign-in attempts. Wait a few minutes and try again.";
  }
  // No status / 0: network failure (AuthRetryableFetchError); 5xx: Auth is down.
  if (!error?.status || error.status >= 500) {
    return "Sign-in is temporarily unavailable. Try again in a moment.";
  }
  // invalid_credentials, email_not_confirmed, user_banned…: one answer for all.
  return "The email or password is incorrect.";
}
