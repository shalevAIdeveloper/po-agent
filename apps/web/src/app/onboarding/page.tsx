import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { describeApiError, type ErrorMessage } from "@/lib/api/errors";
import { requireUser } from "@/lib/auth/session";

import { logout } from "../app/actions";
import { OnboardingForm } from "./onboarding-form";

export const metadata: Metadata = { title: "Set up your organization · PO Agent" };

export default function OnboardingPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Set up your organization</h1>
        <p className="text-sm text-muted-foreground">
          Your organization holds your purchase orders, customers and team. You&apos;ll be its owner.
        </p>
      </header>
      <Suspense fallback={<LoadingState label="Loading" lines={3} />}>
        <Onboarding />
      </Suspense>
    </main>
  );
}

async function Onboarding() {
  const user = await requireUser();

  let failure: ErrorMessage | null = null;
  let hasOrganization = false;
  try {
    const me = await api.getMe(user.accessToken);
    hasOrganization = me.organizations.length > 0;
  } catch (error) {
    failure = describeApiError(error);
  }

  // Already a member (e.g. added by an admin): onboarding is only for users without one.
  if (hasOrganization) {
    redirect("/app");
  }
  if (failure) {
    return (
      <ErrorState
        title={failure.title}
        description={failure.description}
        action={
          <Button variant="outline" nativeButton={false} render={<a href="/onboarding" />}>
            Try again
          </Button>
        }
      />
    );
  }
  return (
    <>
      {/* Visible on purpose: the org is created in *this* account (sec L, 0.4c-2). */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
        <span className="text-muted-foreground">
          {user.email ? (
            <>
              Signed in as <strong className="font-medium text-foreground">{user.email}</strong>.
              Not you?
            </>
          ) : (
            "Not your account?"
          )}
        </span>
        <form action={logout}>
          <Button type="submit" variant="outline" size="sm">
            Sign out
          </Button>
        </form>
      </div>
      <OnboardingForm />
    </>
  );
}
