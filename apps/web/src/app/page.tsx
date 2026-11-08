import Link from "next/link";
import { Suspense } from "react";

import { HealthStatus, HealthStatusSkeleton } from "@/components/health-status";

export default function Home() {
  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">PO Agent</h1>
        <p className="text-sm text-muted-foreground">
          Purchase orders in, validated sales orders out.
        </p>
      </header>
      <Suspense fallback={<HealthStatusSkeleton />}>
        <HealthStatus />
      </Suspense>
      <p>
        <Link href="/app" className="rounded-sm text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          Sign in
        </Link>
      </p>
    </main>
  );
}
