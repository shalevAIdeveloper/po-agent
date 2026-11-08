/**
 * Password policy (decided 2026-11-08, NIST 800-63B): at least 12 characters, no
 * composition rules. Enforced here in characters (code points). Supabase Auth's own
 * `minimum_password_length = 12` counts BYTES (checked against local GoTrue: 11 × "€"
 * is accepted), so it is a weaker backstop for non-ASCII passwords set by calling
 * Auth directly. Max 72 bytes: Auth rejects longer ones (bcrypt's limit).
 */
export const PASSWORD_MIN_CHARS = 12;
export const PASSWORD_MAX_BYTES = 72;

/** Characters as a person counts them (code points, so an emoji counts once). */
export function passwordLength(password: string): number {
  return [...password].length;
}

export function passwordBytes(password: string): number {
  return new TextEncoder().encode(password).length;
}

export type NewPasswordErrors = Partial<Record<"password" | "confirm", string>>;

export function validateNewPassword(password: string, confirm: string): NewPasswordErrors {
  const errors: NewPasswordErrors = {};
  if (passwordLength(password) < PASSWORD_MIN_CHARS) {
    errors.password = `Use at least ${PASSWORD_MIN_CHARS} characters.`;
  } else if (passwordBytes(password) > PASSWORD_MAX_BYTES) {
    errors.password = "That password is too long. Use a shorter one (up to about 70 characters).";
  }
  if (!errors.password && password !== confirm) {
    errors.confirm = "The passwords don't match.";
  }
  return errors;
}

/** Maps a Supabase `updateUser({ password })` failure to one message. */
export function passwordUpdateErrorMessage(error: {
  status?: number;
  code?: string;
  /** AuthWeakPasswordError: "length" | "characters" | "pwned". */
  reasons?: unknown;
}): string {
  switch (error.code) {
    case "weak_password": {
      const reasons = Array.isArray(error.reasons) ? error.reasons : [];
      if (reasons.includes("pwned")) {
        return "That password has appeared in a data breach. Choose another one.";
      }
      if (reasons.includes("length")) {
        return `Use at least ${PASSWORD_MIN_CHARS} characters.`;
      }
      return "That password is too weak. Choose another one.";
    }
    case "same_password":
      return "Choose a password different from your current one.";
    case "reauthentication_needed":
    case "reauthentication_not_valid":
      return "For your security, request a new link and try again.";
  }
  if (error.status === 429 || error.code === "over_request_rate_limit") {
    return "Too many attempts. Wait a few minutes and try again.";
  }
  if (error.status === 401 || error.status === 403) {
    return "Your link has expired. Request a new one.";
  }
  return "We couldn't save your password. Try again in a moment.";
}
