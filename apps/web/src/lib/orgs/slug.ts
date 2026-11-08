/**
 * Organization slugs (they become part of the inbound address in M6).
 * Mirrors the API/DB rule; the DB stays the source of truth (reserved words too).
 */
export const SLUG_MIN = 3;
export const SLUG_MAX = 40;
export const SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function isValidSlug(slug: string): boolean {
  return slug.length >= SLUG_MIN && slug.length <= SLUG_MAX && SLUG_PATTERN.test(slug);
}

/**
 * A suggestion from the organization name: ASCII letters/digits and single hyphens.
 * Returns "" when nothing usable remains (e.g. a name in Hebrew): the user types one.
 */
export function deriveSlug(name: string): string {
  const ascii = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "") // strip accents: "Café" → "Cafe"
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  let slug = ascii;
  if (slug.length > SLUG_MAX) {
    // Cut at a hyphen when possible, never leaving a trailing one.
    const cut = slug.slice(0, SLUG_MAX);
    const lastHyphen = cut.lastIndexOf("-");
    slug = (lastHyphen >= SLUG_MIN ? cut.slice(0, lastHyphen) : cut).replace(/-+$/g, "");
  }
  return slug.length >= SLUG_MIN ? slug : "";
}
