import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorState } from "@/components/states/error-state";
import { LoadingState } from "@/components/states/loading-state";

describe("state components", () => {
  it("LoadingState renders the requested number of skeleton lines", () => {
    const { container } = render(<LoadingState label="Loading orders" lines={4} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading orders");
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(4);
  });

  it("EmptyState renders title, description and action", () => {
    render(
      <EmptyState title="No orders yet" description="Upload a PO to start." action={<button>Upload</button>} />,
    );

    expect(screen.getByText("No orders yet")).toBeInTheDocument();
    expect(screen.getByText("Upload a PO to start.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
  });

  it("ErrorState is announced as an alert", () => {
    render(<ErrorState title="Could not load orders" />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not load orders");
  });
});
