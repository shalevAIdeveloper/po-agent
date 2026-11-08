import { ApiError } from "@/lib/api/client";

export type ErrorMessage = { title: string; description: string };

/** User-facing text for an API failure. Never includes response content. */
export function describeApiError(error: unknown): ErrorMessage {
  if (!(error instanceof ApiError)) {
    return { title: "Something went wrong", description: "Try again in a moment." };
  }

  switch (error.kind) {
    case "network":
      return {
        title: "Can't reach PO Agent",
        description: "The service did not respond. Check your connection and try again.",
      };
    case "invalid_response":
      return {
        title: "Unexpected response",
        description: "The service returned something we couldn't read. Try again in a moment.",
      };
    case "http":
      return describeHttpError(error);
  }
}

function describeHttpError({ status, code, retryAfterSeconds }: ApiError): ErrorMessage {
  if (status === 503 && code === "busy") {
    const wait =
      retryAfterSeconds === undefined
        ? "in a moment"
        : `in ${retryAfterSeconds} second${retryAfterSeconds === 1 ? "" : "s"}`;
    return { title: "The service is busy", description: `Try again ${wait}.` };
  }
  if (status === 503) {
    return {
      title: "Sign-in is temporarily unavailable",
      description: "We couldn't verify your session. Try again in a moment.",
    };
  }
  if (status === 401) {
    return { title: "Your session has ended", description: "Sign in again to continue." };
  }
  if (status === 403) {
    return {
      title: "Not allowed",
      description: "Your role in this organization doesn't allow this action.",
    };
  }
  if (status === 404) {
    return { title: "Not found", description: "It doesn't exist, or you don't have access to it." };
  }
  return {
    title: "Something went wrong",
    description: `The service responded with an error (HTTP ${status ?? "unknown"}).`,
  };
}
