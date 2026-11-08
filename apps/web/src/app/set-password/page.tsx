import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";
import { requireUser } from "@/lib/auth/session";

import { SetPasswordForm } from "./set-password-form";

export const metadata: Metadata = { title: "Choose a password · PO Agent" };

export default function SetPasswordPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-8">
      <h1 className="text-2xl font-semibold">Choose a password</h1>
      <Suspense fallback={<LoadingState label="Loading" lines={3} />}>
        <SetPassword />
      </Suspense>
    </main>
  );
}

async function SetPassword() {
  const user = await requireUser();

  // Only reachable right after an invite/recovery link (see the action).
  if (!user.recentEmailLinkSignIn) {
    return (
      <ErrorState
        title="Open a link from your email first"
        description="For your security, you can choose a new password only within 15 minutes of opening a password link."
        action={
          <Link
            href="/forgot-password"
            className="rounded-sm text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            Send me a new link
          </Link>
        }
      />
    );
  }

  return (
    <>
      {/* Visible on purpose: someone may have sent a link for *their* account. */}
      <p className="text-sm text-muted-foreground">
        {user.email ? (
          <>
            Setting the password for <strong className="font-medium text-foreground">{user.email}</strong>.
            Not you? Don&apos;t continue.
          </>
        ) : (
          "You'll use it to sign in to PO Agent."
        )}
      </p>
      <SetPasswordForm email={user.email} />
    </>
  );
}
