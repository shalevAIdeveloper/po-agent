import "server-only";

import { redirect } from "next/navigation";
import { connection } from "next/server";

import { LOGIN_PATH } from "@/lib/auth/routes";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export type SignedInUser = {
  userId: string;
  email: string | null;
  /** For server-side API calls only. Never pass it to a Client Component. */
  accessToken: string;
};

/**
 * The verified current user, or a redirect to /login. Call it in every protected
 * page and Server Action: proxy.ts redirects too, but is not the boundary.
 */
export async function requireUser(): Promise<SignedInUser> {
  // Only at request time: never during a prerender or a prefetch. Token verification
  // reads the clock (Date.now), and session-bound data must not be prefetched/cached.
  await connection();
  const supabase = await createSupabaseServerClient();

  // getSession() only supplies the raw access token (refreshing it once if needed);
  // nothing from it is trusted. Identity comes from getClaims(token), which verifies
  // that exact JWT's signature and expiry. One refresh path: no refresh-token race.
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const accessToken = session?.access_token;
  if (!accessToken) {
    redirect(LOGIN_PATH);
  }

  // getClaims() rethrows non-auth errors (e.g. an unknown `alg`): treat any failure
  // as signed out instead of a 500. redirect() stays outside the try (it throws too).
  let sub: string | undefined;
  let email: unknown;
  try {
    const { data } = await supabase.auth.getClaims(accessToken);
    sub = data?.claims.sub;
    email = data?.claims.email;
  } catch {
    sub = undefined;
  }
  if (!sub) {
    redirect(LOGIN_PATH);
  }

  return {
    userId: sub,
    email: typeof email === "string" ? email : null,
    accessToken,
  };
}
