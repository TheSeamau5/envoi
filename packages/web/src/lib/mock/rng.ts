/**
 * Deterministic seeded PRNG using the Mulberry32 algorithm.
 * No Math.random() or Date.now() — purely seed-based for reproducibility.
 */

/** A seeded pseudo-random number generator */
export type SeededRng = {
  /** Returns a float in [0, 1) */
  next: () => number;
  /** Returns an integer in [min, max] inclusive */
  nextInt: (min: number, max: number) => number;
  /** Returns true with given probability (default 0.5) */
  nextBool: (probability?: number) => boolean;
  /** Pick a random element from an array */
  pick: <T>(array: readonly T[]) => T;
};

/** Create a deterministic PRNG from a numeric seed */
export function createRng(seed: number): SeededRng {
  let state = seed | 0;

  function next(): number {
    state = (state + 0x6d2b79f5) | 0;
    let temp = Math.imul(state ^ (state >>> 15), 1 | state);
    temp = (temp + Math.imul(temp ^ (temp >>> 7), 61 | temp)) ^ temp;
    return ((temp ^ (temp >>> 14)) >>> 0) / 4294967296;
  }

  function nextInt(min: number, max: number): number {
    return min + Math.floor(next() * (max - min + 1));
  }

  function nextBool(probability = 0.5): boolean {
    return next() < probability;
  }

  function pick<T>(array: readonly T[]): T {
    return array[Math.floor(next() * array.length)]!;
  }

  return { next, nextInt, nextBool, pick };
}

/** Generate a hex hash string of given length */
export function generateHash(rng: SeededRng, length = 8): string {
  const chars = "0123456789abcdef";
  return Array.from({ length }, () => chars[Math.floor(rng.next() * 16)]!).join("");
}
