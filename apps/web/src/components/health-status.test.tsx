import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HealthStatus, HealthStatusSkeleton } from "@/components/health-status";
import { api, ApiError } from "@/lib/api/client";

vi.mock("next/server", () => ({ connection: vi.fn().mockResolvedValue(undefined) }));

describe("HealthStatus", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows that the API is up", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" });

    render(await HealthStatus());

    expect(screen.getByRole("status")).toHaveTextContent(/^API is up$/);
  });

  it("shows an error state when the API is unreachable", async () => {
    vi.spyOn(api, "getHealth").mockRejectedValue(new ApiError("network"));

    render(await HealthStatus());

    expect(screen.getByRole("alert")).toHaveTextContent("API unreachable");
    expect(screen.getByText(/did not respond/)).toBeInTheDocument();
  });

  it("shows an error state on an unexpected response", async () => {
    vi.spyOn(api, "getHealth").mockRejectedValue(new ApiError("invalid_response", { status: 200 }));

    render(await HealthStatus());

    expect(screen.getByRole("alert")).toHaveTextContent("unexpected response");
  });

  it("shows the HTTP status when the API answers with an error", async () => {
    vi.spyOn(api, "getHealth").mockRejectedValue(new ApiError("http", { status: 503 }));

    render(await HealthStatus());

    expect(screen.getByRole("alert")).toHaveTextContent("responded with an error (HTTP 503)");
  });

  it("handles a non-API error without leaking its message", async () => {
    vi.spyOn(api, "getHealth").mockRejectedValue(new Error("ACME 123 Main St"));

    render(await HealthStatus());

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("unexpected response");
    expect(alert).not.toHaveTextContent("ACME");
  });
});

describe("HealthStatusSkeleton", () => {
  it("announces the check to screen readers", () => {
    render(<HealthStatusSkeleton />);

    expect(screen.getByRole("status")).toHaveTextContent("Checking API status");
  });
});
