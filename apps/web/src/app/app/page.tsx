import type { Metadata } from "next";
import { Suspense } from "react";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";
import { Button } from "@/components/ui/button";
import { api, ApiError, type Me } from "@/lib/api/client";
import { describeApiError, type ErrorMessage } from "@/lib/api/errors";
import { requireUser } from "@/lib/auth/session";

import { logout } from "./actions";

export const metadata: Metadata = { title: "Your organizations · PO Agent" };

export default function AppHome() {
  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      {/* Static, so the page has its heading while the session loads. */}
      <h1 className="text-2xl font-semibold">PO Agent</h1>
      <Suspense fallback={<LoadingState label="Loading your account" lines={3} />}>
        <Workspace />
      </Suspense>
    </main>
  );
}

type LoadResult =
  | { status: "ok"; me: Me }
  | { status: "session_ended" }
  | { status: "error"; message: ErrorMessage };

async function loadMe(accessToken: string): Promise<LoadResult> {
  try {
    return { status: "ok", me: await api.getMe(accessToken) };
  } catch (error) {
    // The cookie's token verified here but the API refused it (revoked, or web/API
    // config drift). Not a redirect: /login would bounce straight back to /app.
    if (error instanceof ApiError && error.status === 401) {
      return { status: "session_ended" };
    }
    return { status: "error", message: describeApiError(error) };
  }
}

function SignOutButton({ label = "Sign out" }: { label?: string }) {
  return (
    <form action={logout}>
      <Button type="submit" variant="outline">
        {label}
      </Button>
    </form>
  );
}

async function Workspace() {
  const user = await requireUser();
  const result = await loadMe(user.accessToken);

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-4">
        {user.email ? (
          <p className="text-sm text-muted-foreground">Signed in as {user.email}</p>
        ) : (
          <span />
        )}
        <SignOutButton />
      </header>
      {result.status === "ok" ? <Organizations me={result.me} /> : null}
      {result.status === "session_ended" ? (
        <ErrorState
          title="Your session has ended"
          description="Sign in again to continue."
          action={<SignOutButton label="Sign in again" />}
        />
      ) : null}
      {result.status === "error" ? (
        <ErrorState
          title={result.message.title}
          description={result.message.description}
          action={
            // A full reload re-runs the request; no client JS needed.
            <Button variant="outline" nativeButton={false} render={<a href="/app" />}>
              Try again
            </Button>
          }
        />
      ) : null}
    </>
  );
}

function Organizations({ me }: { me: Me }) {
  const heading = me.organizations.length > 1 ? "Your organizations" : "Your organization";
  return (
    <section aria-labelledby="orgs-heading" className="flex flex-col gap-3">
      <h2 id="orgs-heading" className="text-lg font-medium">
        {heading}
      </h2>
      {me.organizations.length === 0 ? (
        <EmptyState
          title="No organization yet"
          description="You're not a member of any organization. Ask your organization's admin to invite you."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {me.organizations.map((org) => (
            <li key={org.id} className="flex items-center justify-between rounded-lg border p-4">
              <span className="font-medium">{org.name}</span>
              <span className="text-sm text-muted-foreground">
                <span className="sr-only">Role: </span>
                {org.role}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
