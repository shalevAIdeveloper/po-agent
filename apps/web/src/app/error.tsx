"use client";

import { ErrorState } from "@/components/states/error-state";
import { Button } from "@/components/ui/button";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

// Never render error.message: in production it can include server details.
export default function ErrorPage({ error, reset }: ErrorPageProps) {
  return (
    <main className="mx-auto max-w-2xl p-8">
      <ErrorState
        title="Something went wrong"
        description={error.digest ? `Reference: ${error.digest}` : undefined}
        action={<Button onClick={() => reset()}>Try again</Button>}
      />
    </main>
  );
}
