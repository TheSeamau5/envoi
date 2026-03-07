/**
 * Stale-while-revalidate in-memory cache for API responses.
 *
 * Always returns immediately — if the entry is stale the caller still gets the
 * old data while a background revalidation refreshes the cache for the next
 * request.  This keeps page loads fast even when the underlying query is slow.
 */

type CacheEntry<T> = {
  data: T;
  timestamp: number;
};

// Store on globalThis to survive Turbopack HMR re-evaluations in dev mode.
type CacheGlobals = {
  envoiCacheStore: Map<string, CacheEntry<unknown>>;
  envoiCacheRevalidating: Set<string>;
};
const cg = globalThis as unknown as Partial<CacheGlobals>;
const store = (cg.envoiCacheStore ??= new Map<string, CacheEntry<unknown>>());
const revalidating = (cg.envoiCacheRevalidating ??= new Set<string>());
const DEFAULT_TTL_MS = 5 * 60_000; // 5 minutes

/**
 * Return cached data immediately.  If expired, kick off a background
 * revalidation so the next caller gets fresh data (stale-while-revalidate).
 * On a complete cache miss the first call awaits the fetch.
 */
export async function cached<T>(
  key: string,
  fn: () => Promise<T>,
  ttlMs: number = DEFAULT_TTL_MS,
): Promise<T> {
  const now = Date.now();
  const existing = store.get(key);

  if (existing) {
    if (now - existing.timestamp >= ttlMs && !revalidating.has(key)) {
      revalidating.add(key);
      fn()
        .then((data) => store.set(key, { data, timestamp: Date.now() }))
        .catch(() => {})
        .finally(() => revalidating.delete(key));
    }
    return existing.data as T;
  }

  const data = await fn();
  store.set(key, { data, timestamp: now });
  return data;
}

/** Clear all cached entries (call after refresh) */
export function clearCache(): void {
  store.clear();
}
