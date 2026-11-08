/**
 * How the current session was established, from the JWT `amr` claim. Supabase Auth
 * records invite and recovery links as `{ method: "otp", timestamp }`, and keeps that
 * entry (with its original timestamp) across token refreshes. Checked against local
 * GoTrue (0.4c-2).
 */
export const LINK_SIGN_IN_WINDOW_SECONDS = 15 * 60;

type AmrEntry = { method?: unknown; timestamp?: unknown };

/**
 * True only if the session came from an email link (invite / recovery) opened in the
 * last 15 minutes. /set-password requires it, so a stolen or unattended session
 * cannot silently replace the password (it would need the current one).
 */
export function signedInByRecentEmailLink(
  amr: unknown,
  nowSeconds: number,
  windowSeconds = LINK_SIGN_IN_WINDOW_SECONDS,
): boolean {
  if (!Array.isArray(amr)) {
    return false;
  }
  return amr.some((entry: AmrEntry) => {
    if (entry?.method !== "otp" || typeof entry.timestamp !== "number") {
      return false;
    }
    const age = nowSeconds - entry.timestamp;
    // Small negative ages: clock skew between Auth and this server.
    return age >= -60 && age <= windowSeconds;
  });
}
