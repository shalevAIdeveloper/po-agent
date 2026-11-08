import { describe, expect, it } from "vitest";

import { fieldErrorsFrom, formString, nextAttempt } from "@/lib/forms/form-state";

describe("nextAttempt", () => {
  it.each([
    [{ attempt: 0 }, 1],
    [{ attempt: 4 }, 5],
    // Client-controlled garbage restarts at 1 instead of producing NaN / "x1".
    [{ attempt: "x" }, 1],
    [{ attempt: -3 }, 1],
    [{ attempt: 1.5 }, 1],
    [{ attempt: Number.MAX_VALUE }, 1],
    [{}, 1],
    [null, 1],
    [undefined, 1],
  ])("%j → %d", (previous, expected) => {
    expect(nextAttempt(previous as { attempt?: unknown } | null | undefined)).toBe(expected);
  });
});

describe("fieldErrorsFrom", () => {
  it("keeps the first message per known field and ignores others", () => {
    const issues = [
      { path: ["name"], message: "first" },
      { path: ["name"], message: "second" },
      { path: ["slug"], message: "slug!" },
      { path: ["other"], message: "ignored" },
      { path: [], message: "form-level" },
    ];

    expect(fieldErrorsFrom(issues, ["name", "slug"] as const)).toEqual({
      name: "first",
      slug: "slug!",
    });
  });
});

describe("formString", () => {
  it("returns strings only", () => {
    const data = new FormData();
    data.set("a", "x");
    data.set("file", new Blob(["y"]));

    expect(formString(data, "a")).toBe("x");
    expect(formString(data, "file")).toBe("");
    expect(formString(data, "missing")).toBe("");
  });
});
