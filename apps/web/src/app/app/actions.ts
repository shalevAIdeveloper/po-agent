"use server";

import { redirect } from "next/navigation";

import { LOGIN_PATH } from "@/lib/auth/routes";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export async function logout(): Promise<void> {
  const supabase = await createSupabaseServerClient();
  // `local`: ends this browser's session (revokes its refresh token) and clears the
  // cookies. If Auth is unreachable the cookies are still cleared.
  await supabase.auth.signOut({ scope: "local" });
  redirect(LOGIN_PATH);
}
