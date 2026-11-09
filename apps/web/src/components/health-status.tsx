import { connection } from "next/server";

import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";
import { api, ApiError, type ApiErrorKind } from "@/lib/api/client";

type HealthFailure = { ok: false; kind: ApiErrorKind | "unknown"; status?: number };
type HealthResult = { ok: true } | HealthFailure;

async function loadHealth(): Promise<HealthResult> {
  try {
    await api.getHealth();
    return { ok: true };
  } catch (error) {
    return error instanceof ApiError
      ? { ok: false, kind: error.kind, status: error.status }
      : { ok: false, kind: "unknown" };
  }
}

function failureDescription({ kind, status }: HealthFailure): string {
  switch (kind) {
    case "network":
      return "The API did not respond. Check that it is running.";
    case "http":
      return `The API responded with an error (HTTP ${status ?? "unknown"}).`;
    case "invalid_response":
    case "unknown":
      return "The API returned an unexpected response.";
  }
}

/** Server component: checks the API on every request (never prerendered). */
export async function HealthStatus() {
  await connection();
  const result = await loadHealth();

  if (!result.ok) {
    return <ErrorState title="API unreachable" description={failureDescription(result)} />;
  }

  return (
    <div
      role="status"
      data-slot="health-status"
      className="flex items-center gap-2 rounded-lg border p-4 text-sm"
    >
      <span aria-hidden="true" className="size-2 rounded-full bg-status-success" />
      <span>API is up</span>
    </div>
  );
}

export function HealthStatusSkeleton() {
  return <LoadingState label="Checking API status" lines={1} />;
}
