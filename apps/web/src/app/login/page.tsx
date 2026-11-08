import type { Metadata } from "next";
import { Suspense } from "react";

import { LoadingState } from "@/components/states/loading-state";
import { safeNextPath } from "@/lib/auth/next-path";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in · PO Agent" };

export default function LoginPage({ searchParams }: PageProps<"/login">) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Sign in to PO Agent</h1>
        <p className="text-sm text-muted-foreground">
          Access is by invitation. Use the email address you were invited with.
        </p>
      </header>
      <Suspense fallback={<LoadingState label="Loading sign-in form" lines={3} />}>
        <LoginFormWithNext searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

/** Reads `next` at request time (behind Suspense, as Cache Components requires). */
async function LoginFormWithNext({ searchParams }: Pick<PageProps<"/login">, "searchParams">) {
  const { next } = await searchParams;
  // Sanitized here for the hidden field, and again in the action on submit.
  const safeNext = typeof next === "string" ? safeNextPath(next) : undefined;
  return <LoginForm next={safeNext} />;
}
