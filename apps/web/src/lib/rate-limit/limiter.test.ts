// @vitest-environment node
import { describe, expect, it } from "vitest";

import { SlidingWindowLimiter } from "./limiter";

function clock(start = 1_000_000) {
  let now = start;
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
}

describe("SlidingWindowLimiter", () => {
  it("allows `limit` events per window, then blocks", () => {
    const limiter = new SlidingWindowLimiter({ limit: 3, windowMs: 1000 }, clock().now);

    expect([1, 2, 3, 4].map(() => limiter.take("a"))).toEqual([true, true, true, false]);
    expect(limiter.isLimited("a")).toBe(true);
  });

  it("frees one slot as each event leaves the window (sliding, not fixed)", () => {
    const time = clock();
    const limiter = new SlidingWindowLimiter({ limit: 2, windowMs: 1000 }, time.now);

    limiter.take("a");
    time.advance(600);
    limiter.take("a");
    expect(limiter.take("a")).toBe(false);

    time.advance(400); // the first event is now exactly windowMs old: out
    expect(limiter.take("a")).toBe(true);
    expect(limiter.take("a")).toBe(false); // the second is still inside
  });

  it("keeps keys apart", () => {
    const limiter = new SlidingWindowLimiter({ limit: 1, windowMs: 1000 }, clock().now);

    expect(limiter.take("a")).toBe(true);
    expect(limiter.take("b")).toBe(true);
    expect(limiter.take("a")).toBe(false);
  });

  it("does not record blocked attempts, so blocking ends one window after the last allowed one", () => {
    const time = clock();
    const limiter = new SlidingWindowLimiter({ limit: 1, windowMs: 1000 }, time.now);

    limiter.take("a");
    for (let i = 0; i < 5; i++) {
      time.advance(150);
      expect(limiter.take("a")).toBe(false);
    }
    time.advance(250); // 1000 ms after the only recorded event
    expect(limiter.take("a")).toBe(true);
  });

  it("hit records unconditionally and reset forgets the key", () => {
    const limiter = new SlidingWindowLimiter({ limit: 2, windowMs: 1000 }, clock().now);

    limiter.hit("a");
    limiter.hit("a");
    expect(limiter.isLimited("a")).toBe(true);

    limiter.reset("a");
    expect(limiter.isLimited("a")).toBe(false);
    expect(limiter.size).toBe(0);
  });

  it("drops keys whose events all expired", () => {
    const time = clock();
    const limiter = new SlidingWindowLimiter({ limit: 2, windowMs: 1000 }, time.now);

    limiter.hit("a");
    time.advance(1000);
    expect(limiter.isLimited("a")).toBe(false);
    expect(limiter.size).toBe(0);
  });

  it("release gives back exactly the reserved event, not a newer one", () => {
    const time = clock();
    const limiter = new SlidingWindowLimiter({ limit: 3, windowMs: 1000 }, time.now);

    const mine = limiter.reserve("a");
    time.advance(300);
    limiter.take("a"); // someone else's event, newer than mine
    limiter.release("a", mine!);
    expect(limiter.count("a")).toBe(1);

    time.advance(700); // mine would have left the window now; the other one is kept
    expect(limiter.count("a")).toBe(1);
  });

  it("release does nothing once the event is gone", () => {
    const time = clock();
    const limiter = new SlidingWindowLimiter({ limit: 2, windowMs: 1000 }, time.now);

    const mine = limiter.reserve("a")!;
    limiter.reset("a");
    time.advance(1); // later events have other timestamps than the reset one
    limiter.take("a");
    limiter.take("a");
    limiter.release("a", mine);
    expect(limiter.count("a")).toBe(2);

    limiter.release("nobody", mine);
    expect(limiter.size).toBe(1);
  });

  it("reserve returns null at the limit; releasing the only event drops the key", () => {
    const limiter = new SlidingWindowLimiter({ limit: 1, windowMs: 1000 }, clock().now);

    const mine = limiter.reserve("a")!;
    expect(limiter.reserve("a")).toBeNull();
    limiter.release("a", mine);
    expect(limiter.size).toBe(0);
  });

  it("a refused key counts as recently used, so a key under attack isn't evicted first", () => {
    const limiter = new SlidingWindowLimiter({ limit: 1, windowMs: 1000 }, clock().now, 2);

    limiter.take("victim");
    limiter.take("b");
    expect(limiter.take("victim")).toBe(false); // refused, but now the most recent
    limiter.take("c"); // evicts "b", not "victim"

    expect(limiter.isLimited("victim")).toBe(true);
    expect(limiter.isLimited("b")).toBe(false);
  });

  it("holds at most maxKeys keys, evicting the least recently hit", () => {
    const limiter = new SlidingWindowLimiter({ limit: 1, windowMs: 1000 }, clock().now, 2);

    limiter.hit("a");
    limiter.hit("b");
    limiter.hit("a"); // "a" is now the most recent
    limiter.hit("c"); // evicts "b"

    expect(limiter.size).toBe(2);
    expect(limiter.isLimited("a")).toBe(true);
    expect(limiter.isLimited("b")).toBe(false);
    expect(limiter.isLimited("c")).toBe(true);
  });

  it("keeps at most `limit` timestamps per key", () => {
    const limiter = new SlidingWindowLimiter({ limit: 2, windowMs: 1000 }, clock().now);

    for (let i = 0; i < 10; i++) limiter.hit("a");
    expect(limiter.count("a")).toBe(2);
  });

  it.each([
    { limit: 0, windowMs: 1000 },
    { limit: 1.5, windowMs: 1000 },
    { limit: 1, windowMs: 0 },
    { limit: 1, windowMs: Number.NaN },
  ])("rejects an invalid rule %o", (rule) => {
    expect(() => new SlidingWindowLimiter(rule)).toThrow("invalid rate rule");
  });
});
