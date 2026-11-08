import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { decideRoute, LOGIN_PATH } from "@/lib/auth/routes";
import { env } from "@/lib/env";

/**
 * Refreshes the Supabase session on every matched request and applies the route
 * rules. This is the only place the session cookies are rewritten during a page
 * load (Server Components cannot set cookies).
 *
 * The redirect here is a convenience, not the security boundary: pages and Server
 * Actions verify the user again (`getClaims()`), and the API verifies the JWT.
 */
export async function updateSession(request: NextRequest): Promise<NextResponse> {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    env.NEXT_PUBLIC_SUPABASE_URL,
    env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (cookiesToSet, headers) => {
          // Downstream Server Components read the refreshed token from the request…
          for (const { name, value } of cookiesToSet) {
            request.cookies.set(name, value);
          }
          // …and the browser gets it from the response.
          response = NextResponse.next({ request });
          for (const { name, value, options } of cookiesToSet) {
            response.cookies.set(name, value, options);
          }
          // Cache-Control etc.: a response that sets auth cookies must never be cached.
          for (const [key, value] of Object.entries(headers)) {
            response.headers.set(key, value);
          }
        },
      },
    },
  );

  // Nothing between creating the client and getClaims(): it refreshes an expiring
  // token and verifies the JWT signature (never trust getSession() here).
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims.sub);

  const decision = decideRoute(request.nextUrl.pathname, signedIn, request.method);
  if (decision.action === "next") {
    return response;
  }

  const target = request.nextUrl.clone();
  target.pathname = decision.to;
  target.search = "";
  target.hash = "";
  if (decision.withNext && decision.to === LOGIN_PATH) {
    target.searchParams.set("next", `${request.nextUrl.pathname}${request.nextUrl.search}`);
  }
  return withSessionCookies(NextResponse.redirect(target), response);
}

/** A new response loses refreshed cookies and no-cache headers unless copied over. */
function withSessionCookies(redirect: NextResponse, from: NextResponse): NextResponse {
  for (const cookie of from.cookies.getAll()) {
    redirect.cookies.set(cookie);
  }
  for (const key of ["cache-control", "expires", "pragma"]) {
    const value = from.headers.get(key);
    if (value !== null) {
      redirect.headers.set(key, value);
    }
  }
  return redirect;
}
