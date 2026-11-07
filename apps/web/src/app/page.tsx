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
    </main>
  );
}
