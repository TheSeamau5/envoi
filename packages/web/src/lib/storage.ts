/**
 * LocalStorage persistence hook with SSR safety and error handling.
 * Always falls back to defaults — never crashes on bad/missing/outdated data.
 *
 * Initializes directly from localStorage on the client (lazy useState initializer).
 * On the server, always returns defaultValue.
 * Persistence is inline in the setter callback — ZERO useEffect.
 */

"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Prefix all envoi localStorage keys to avoid collisions */
const STORAGE_PREFIX = "envoi:";

/** Type guard: validates that runtime typeof matches the sample value */
function isSameType<T>(value: unknown, sample: T): value is T {
  return typeof value === typeof sample;
}

/**
 * Read a stored value from localStorage, returning undefined if not found
 * or invalid. Pure function with no side effects.
 */
function readStoredValue<T>(prefixedKey: string, defaultValue: T): T | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  try {
    const stored = window.localStorage.getItem(prefixedKey);
    if (stored === null) {
      return undefined;
    }
    const parsed: unknown = JSON.parse(stored);
    if (isSameType(parsed, defaultValue)) {
      return parsed;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

/**
 * Subscribe to storage events so useSyncExternalStore knows when to re-read.
 * We also dispatch a custom event on writes so same-tab updates propagate.
 */
const STORAGE_EVENT = "envoi-storage";
function subscribeToStorage(callback: () => void): () => void {
  /** Cross-tab: native storage event */
  window.addEventListener("storage", callback);
  /** Same-tab: custom event dispatched by our setter */
  window.addEventListener(STORAGE_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(STORAGE_EVENT, callback);
  };
}

/**
 * React hook that persists state to localStorage.
 * Uses useSyncExternalStore for hydration-safe reads — the server snapshot
 * always returns defaultValue, so SSR and first client render match.
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, (valueOrUpdater: T | ((prev: T) => T)) => void] {
  const prefixedKey = `${STORAGE_PREFIX}${key}`;

  const getSnapshot = useCallback((): T => {
    const stored = readStoredValue(prefixedKey, defaultValue);
    return stored !== undefined ? stored : defaultValue;
  }, [prefixedKey, defaultValue]);

  const getServerSnapshot = useCallback((): T => {
    return defaultValue;
  }, [defaultValue]);

  const value = useSyncExternalStore(subscribeToStorage, getSnapshot, getServerSnapshot);

  const setPersistedValue = useCallback(
    (valueOrUpdater: T | ((prev: T) => T)) => {
      const current = readStoredValue(prefixedKey, defaultValue) ?? defaultValue;
      const next =
        typeof valueOrUpdater === "function"
          ? (valueOrUpdater as (prev: T) => T)(current)
          : valueOrUpdater;
      try {
        window.localStorage.setItem(prefixedKey, JSON.stringify(next));
      } catch {
        /** Storage full or blocked — silently ignore */
      }
      /** Notify same-tab subscribers */
      window.dispatchEvent(new Event(STORAGE_EVENT));
    },
    [prefixedKey, defaultValue],
  );

  return [value, setPersistedValue];
}
