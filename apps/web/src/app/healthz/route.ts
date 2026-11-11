/**
 * Liveness for the platform health check (Railway): answers as soon as the server
 * runs, without the API, Supabase or a session. Excluded from the proxy matcher.
 * Reveals nothing about the deployment (same rule as the API's /health, L8).
 */
export function GET(): Response {
  return Response.json({ status: "ok" }, { headers: { "cache-control": "no-store" } });
}
