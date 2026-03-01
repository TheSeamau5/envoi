/**
 * Simple time-based in-memory cache for API responses.
 *
 * Prevents redundant DuckDB queries when multiple users load the dashboard
 * within the same TTL window. The cache is process-scoped and survives
 * across requests but is cleared on server restart.
 */

type CacheEntry<T> = {
  data: T;
  timestamp: number;
};

const store = new Map<string, CacheEntry<unknown>>();
const DEFAULT_TTL_MS = 30_000; // 30 seconds

/**
 * Return a cached value if fresh, otherwise compute and cache the result.
 * The key should encode all query parameters that affect the result.
 */
export async function cached<T>(
  key: string,
  fn: () => Promise<T>,
  ttlMs: number = DEFAULT_TTL_MS,
): Promise<T> {
  const now = Date.now();
  const existing = store.get(key);

  if (existing && now - existing.timestamp < ttlMs) {
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
