import { describe, expect, it } from "vitest";

import { GET } from "@/app/healthz/route";
import { config } from "@/proxy";

describe("GET /healthz", () => {
  it("returns only the status, uncached", async () => {
    const response = GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("is not handled by the session proxy", () => {
    const [catchAll] = config.matcher;
    // Next anchors matcher sources the same way: the whole pathname must match.
    const matches = (path: string) => new RegExp(`^${catchAll}$`).test(path);

    expect(matches("/healthz")).toBe(false);
    expect(matches("/healthz-other")).toBe(true);
    expect(matches("/login")).toBe(true);
  });
});
