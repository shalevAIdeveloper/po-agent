import type { Metadata } from "next";
import { Suspense } from "react";

import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";
import { EMAIL_LINK_COPY, parseEmailLink } from "@/lib/auth/email-link";

import { ConfirmForm, RequestNewLink } from "./confirm-form";

export const metadata: Metadata = {
  title: "Continue · PO Agent",
  // The URL carries a one-time token: never send it to another site as a Referer.
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function ConfirmPage({ searchParams }: PageProps<"/auth/confirm">) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-8">
      <Suspense fallback={<LoadingState label="Checking your link" lines={3} />}>
        <Confirm searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

async function Confirm({ searchParams }: Pick<PageProps<"/auth/confirm">, "searchParams">) {
  const params = await searchParams;
  // GET never verifies: it only shows the button (scanners must not use up the link).
  const link = parseEmailLink(params.type, params.token_hash);

  if (!link) {
    return (
      <>
        <h1 className="text-2xl font-semibold">This link doesn&apos;t work</h1>
        <ErrorState
          title="This link is not valid"
          description="It may be incomplete. Open the link from the email again, or request a new one."
          action={<RequestNewLink />}
        />
      </>
    );
  }

  const copy = EMAIL_LINK_COPY[link.type];
  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">{copy.title}</h1>
        <p className="text-sm text-muted-foreground">{copy.intro}</p>
      </header>
      <ConfirmForm link={link} actionLabel={copy.action} />
    </>
  );
}
