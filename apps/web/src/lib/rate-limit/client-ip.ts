import { isIP } from "node:net";

/**
 * The client IP as a rate-limit key, from Railway's edge (ADR 0009).
 *
 * Only `X-Real-IP` is read: Railway documents it as the client's address and its edge
 * overwrites any value the client sent. `X-Forwarded-For` is never read (its left part
 * is client-controlled). A missing, repeated (comma-joined) or malformed value gives
 * `null`; callers then use one shared bucket, never "no limit".
 *
 * IPv6 addresses are reduced to their /64: one subscriber usually gets a whole /64, so
 * per-address keys would let them rotate through 2^64 buckets.
 */
export function clientIpKey(headers: { get(name: string): string | null }): string | null {
  const raw = headers.get("x-real-ip")?.trim();
  if (!raw) return null;

  const version = isIP(raw);
  if (version === 4) return raw;
  if (version !== 6) return null;

  const lower = raw.toLowerCase();
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/.exec(lower)?.[1];
  if (mapped && isIP(mapped) === 4) return mapped;
  return ipv6Slash64(lower);
}

/** "2001:db8:a:b:1:2:3:4" → "2001:db8:a:b::/64". Input is already a valid IPv6. */
function ipv6Slash64(address: string): string {
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

  return `${groups
    .slice(0, 4)
    .map((group) => Number.parseInt(group, 16).toString(16))
    .join(":")}::/64`;
}
