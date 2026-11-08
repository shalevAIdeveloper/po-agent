import type { NextRequest } from "next/server";

import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  return updateSession(request);
}

export const config = {
  matcher: [
    // Everything except Next internals and static files. Server Actions are POSTs to
    // their page's route, so they stay covered (they verify the user themselves too).
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml)$).*)",
    // Everything under /app, even paths with a file extension (e.g. a future export.xml
    // Route Handler), always gets the session refresh and the guard.
    "/app/:path*",
    "/onboarding/:path*",
    "/set-password/:path*",
  ],
};
