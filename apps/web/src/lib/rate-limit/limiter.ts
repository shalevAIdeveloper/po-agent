/** At most `limit` events per key in any `windowMs` window (sliding log). */
export type RateRule = { limit: number; windowMs: number };

/**
 * In-memory sliding-window limiter (ADR 0009). State lives in this process only, so it
 * is correct while the web service runs one replica; a second replica needs a shared
 * store behind the same methods.
 *
 * Memory is bounded: at most `maxKeys` keys (least recently hit evicted first), each
 * holding at most `limit` timestamps.
 */
export class SlidingWindowLimiter {
  private readonly hits = new Map<string, number[]>();

  constructor(
    private readonly rule: RateRule,
    private readonly now: () => number = Date.now,
    private readonly maxKeys = 50_000,
  ) {
    if (!Number.isSafeInteger(rule.limit) || rule.limit < 1 || !(rule.windowMs > 0)) {
      throw new Error("invalid rate rule");
    }
  }

  /** True if the key has used up its window: one more event would go over the limit. */
  isLimited(key: string): boolean {
    return this.recent(key).length >= this.rule.limit;
  }

  /** Records one event for the key. */
  hit(key: string): void {
    const recent = this.recent(key);
    recent.push(this.now());
    this.store(key, recent);
  }

  /**
   * Records one event if the key is under its limit and returns true; otherwise returns
   * false and records nothing (a blocked caller can't grow the state). A refused key
   * still counts as recently used, so a key under attack isn't the first one evicted.
   */
  take(key: string): boolean {
    return this.reserve(key) !== null;
  }

  /**
   * Like `take`, but returns the recorded event (its timestamp), so exactly that event
   * can be given back with `release`; null when the key is at its limit.
   */
  reserve(key: string): number | null {
    const recent = this.recent(key);
    if (recent.length >= this.rule.limit) {
      this.store(key, recent);
      return null;
    }
    const stamp = this.now();
    recent.push(stamp);
    this.store(key, recent);
    return stamp;
  }

  /**
   * Gives back one event reserved with `reserve` (a slot that turned out not to count).
   * Removes that event, not the newest one, so it can't undo another request's event;
   * does nothing if it is already gone (expired or reset).
   */
  release(key: string, stamp: number): void {
    const recent = this.recent(key);
    const index = recent.lastIndexOf(stamp);
    if (index === -1) return;
    recent.splice(index, 1);
    if (recent.length === 0) this.hits.delete(key);
    else this.store(key, recent);
  }

  /** Forgets the key (e.g. failures after a successful sign-in). */
  reset(key: string): void {
    this.hits.delete(key);
  }

  /** Events of the key still inside the window. */
  count(key: string): number {
    return this.recent(key).length;
  }

  /** Number of keys held; for tests. */
  get size(): number {
    return this.hits.size;
  }

  private recent(key: string): number[] {
    const stamps = this.hits.get(key);
    if (!stamps) return [];
    const cutoff = this.now() - this.rule.windowMs;
    const kept = stamps.filter((stamp) => stamp > cutoff);
    if (kept.length === 0) this.hits.delete(key);
    return kept;
  }

  private store(key: string, stamps: number[]): void {
    // Re-insert so Map order is "least recently hit first", then evict from the front.
    this.hits.delete(key);
    this.hits.set(key, stamps.slice(-this.rule.limit));
    while (this.hits.size > this.maxKeys) {
      const oldest = this.hits.keys().next().value;
      if (oldest === undefined) break;
      this.hits.delete(oldest);
    }
  }
}
