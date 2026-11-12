import { isIP } from "node:net";

/** Rate-limit keys for one client. */
export type ClientIp = {
  /** IPv4 address, or the IPv6 /64. */
  key: string;
  /** IPv6 only: the /48, a coarser second key (one /48 holds 65 536 /64s). */
  wide: string | null;
};

export type NoClientIp = { reason: "missing" | "malformed" };

/**
 * The client IP from Railway's edge (ADR 0009).
 *
 * Only `X-Real-IP` is read: Railway documents it as the client's address and its edge
 * overwrites any value the client sent. `X-Forwarded-For` is never read (its left part
 * is client-controlled). A missing, repeated (comma-joined) or malformed value gives a
 * reason instead; callers then use one shared bucket, never "no limit".
 *
 * IPv6 is keyed by its /64 (one subscriber usually gets a whole /64) plus its /48;
 * IPv4 embedded in IPv6 (mapped / translated forms) is keyed as the IPv4 address.
 */
export function parseClientIp(headers: { get(name: string): string | null }): ClientIp | NoClientIp {
  const raw = headers.get("x-real-ip")?.trim();
  if (!raw) return { reason: "missing" };

  const version = isIP(raw);
  if (version === 4) return { key: raw, wide: null };
  if (version !== 6) return { reason: "malformed" };

  const groups = expandIpv6(raw.toLowerCase());
  const embedded = embeddedIpv4(groups);
  if (embedded) return { key: embedded, wide: null };
  return { key: `${groups.slice(0, 4).join(":")}::/64`, wide: `${groups.slice(0, 3).join(":")}::/48` };
}

/** Just the key, or null; for callers that don't need the reason. */
export function clientIpKey(headers: { get(name: string): string | null }): string | null {
  const ip = parseClientIp(headers);
  return "key" in ip ? ip.key : null;
}

/** Eight groups without leading zeros. Input is already a valid IPv6 (`isIP` = 6). */
function expandIpv6(address: string): string[] {
  let addr = address.split("%")[0] ?? address; // drop a zone id
  // A trailing dotted IPv4 part becomes two hex groups.
  const dotted = /(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(addr);
  if (dotted) {
    const [a, b, c, d] = dotted.slice(1).map(Number) as [number, number, number, number];
    addr = `${addr.slice(0, dotted.index)}${((a << 8) | b).toString(16)}:${((c << 8) | d).toString(16)}`;
  }

  const [head = "", tail] = addr.split("::");
  const front = head ? head.split(":") : [];
  const back = tail ? tail.split(":") : [];
  const groups =
    tail === undefined ? front : [...front, ...Array<string>(8 - front.length - back.length).fill("0"), ...back];
  return groups.map((group) => Number.parseInt(group, 16).toString(16));
}

/** `::ffff:a.b.c.d` (mapped) and `::ffff:0:a.b.c.d` (translated), in any spelling. */
function embeddedIpv4(groups: string[]): string | null {
  const zeros = (from: number, to: number) => groups.slice(from, to).every((group) => group === "0");
  const mapped = zeros(0, 5) && groups[5] === "ffff";
  const translated = zeros(0, 4) && groups[4] === "ffff" && groups[5] === "0";
  if (!mapped && !translated) return null;

  const high = Number.parseInt(groups[6] ?? "0", 16);
  const low = Number.parseInt(groups[7] ?? "0", 16);
  return [high >> 8, high & 255, low >> 8, low & 255].join(".");
}
