/**
 * LocalStorage persistence hook with SSR safety and error handling.
 * Always falls back to defaults — never crashes on bad/missing/outdated data.
 *
 * Initializes directly from localStorage on the client (lazy useState initializer).
 * On the server, always returns defaultValue.
 * Persistence is inline in the setter callback — ZERO useEffect.
 */

"use client";

import { useState, useCallback } from "react";

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

/** React hook that persists state to localStorage */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, (valueOrUpdater: T | ((prev: T) => T)) => void] {
  const prefixedKey = `${STORAGE_PREFIX}${key}`;

  /**
   * Lazy initializer: reads localStorage on the client, returns defaultValue
   * on the server. This is a pure read — no side effects, no setState.
   */
  const [value, setValue] = useState<T>(() => {
    const stored = readStoredValue(prefixedKey, defaultValue);
    return stored !== undefined ? stored : defaultValue;
  });

  /**
   * Setter that persists to localStorage inline — no useEffect needed.
   */
  const setPersistedValue = useCallback(
    (valueOrUpdater: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const next =
          typeof valueOrUpdater === "function"
            ? (valueOrUpdater as (prev: T) => T)(prev)
            : valueOrUpdater;
        try {
          window.localStorage.setItem(prefixedKey, JSON.stringify(next));
        } catch {
          /** Storage full or blocked — silently ignore */
        }
        return next;
      });
    },
    [prefixedKey],
  );

  return [value, setPersistedValue];
}
