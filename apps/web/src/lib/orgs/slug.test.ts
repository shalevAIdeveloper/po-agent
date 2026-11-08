import { describe, expect, it } from "vitest";

import { deriveSlug, isValidSlug, SLUG_MAX } from "@/lib/orgs/slug";

describe("deriveSlug", () => {
  it.each([
    ["Acme Supply", "acme-supply"],
    ["  Acme   Supply  Co. ", "acme-supply-co"],
    ["Café Distribución", "cafe-distribucion"],
    ["Smith & Sons", "smith-and-sons"],
    ["ACME_Supply--Inc!!", "acme-supply-inc"],
    ["123 Wholesale", "123-wholesale"],
  ])("%j → %j", (name, slug) => {
    expect(deriveSlug(name)).toBe(slug);
    expect(isValidSlug(slug)).toBe(true);
  });

  it.each(["", "  ", "אקמי הפצה", "日本", "ab", "!!"])(
    "returns empty when nothing usable remains (%j): the user types one",
    (name) => {
      expect(deriveSlug(name)).toBe("");
    },
  );

  it("keeps Latin words from a mixed name", () => {
    expect(deriveSlug("אקמי Acme Supply")).toBe("acme-supply");
  });

  it("cuts long names at a word boundary, within the limit, without a trailing hyphen", () => {
    const slug = deriveSlug("The Very Long Wholesale Distribution Company of North America");

    expect(slug.length).toBeLessThanOrEqual(SLUG_MAX);
    expect(slug).toBe("the-very-long-wholesale-distribution");
    expect(isValidSlug(slug)).toBe(true);
  });

  it("cuts a single very long word at the limit", () => {
    expect(deriveSlug("a".repeat(60))).toBe("a".repeat(SLUG_MAX));
  });
});

describe("isValidSlug", () => {
  it.each(["abc", "acme-supply", "a1-b2", "a".repeat(40)])("accepts %j", (slug) => {
    expect(isValidSlug(slug)).toBe(true);
  });

  it.each(["ab", "a".repeat(41), "Acme", "acme_supply", "-acme", "acme-", "ac--me", "ac me", ""])(
    "rejects %j",
    (slug) => {
      expect(isValidSlug(slug)).toBe(false);
    },
  );
});
