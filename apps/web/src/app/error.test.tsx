import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ErrorPage from "@/app/error";

describe("ErrorPage", () => {
  it("never renders the error message, only the digest", () => {
    const error = Object.assign(new Error("secret ACME 123 Main St"), { digest: "abc123" });

    render(<ErrorPage error={error} reset={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
    expect(screen.getByText("Reference: abc123")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("ACME");
  });

  it("omits the reference line when there is no digest", () => {
    render(<ErrorPage error={new Error("boom")} reset={vi.fn()} />);

    expect(screen.queryByText(/Reference:/)).not.toBeInTheDocument();
  });

  it("calls reset when the user retries", async () => {
    const reset = vi.fn();
    render(<ErrorPage error={new Error("boom")} reset={reset} />);

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(reset).toHaveBeenCalledOnce();
  });
});
