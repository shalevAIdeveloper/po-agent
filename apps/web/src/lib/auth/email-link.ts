/** Email links handled by /auth/confirm. Anything else is rejected, never passed to Auth. */
export const EMAIL_LINK_TYPES = ["invite", "recovery"] as const;
export type EmailLinkType = (typeof EMAIL_LINK_TYPES)[number];

export type EmailLink = { type: EmailLinkType; tokenHash: string };

// Supabase token hashes are hex (optionally `pkce_`-prefixed); allow a safe superset.
const TOKEN_HASH = /^[A-Za-z0-9_-]{16,256}$/;

export function parseEmailLink(type: unknown, tokenHash: unknown): EmailLink | null {
  if (typeof type !== "string" || typeof tokenHash !== "string") {
    return null;
  }
  if (!(EMAIL_LINK_TYPES as readonly string[]).includes(type) || !TOKEN_HASH.test(tokenHash)) {
    return null;
  }
  return { type: type as EmailLinkType, tokenHash };
}

export const EMAIL_LINK_COPY: Record<EmailLinkType, { title: string; intro: string; action: string }> =
  {
    invite: {
      title: "Accept your invitation",
      intro: "You've been invited to PO Agent. Continue to choose your password.",
      action: "Continue",
    },
    recovery: {
      title: "Reset your password",
      intro: "Continue to choose a new password for your account.",
      action: "Continue",
    },
  };
