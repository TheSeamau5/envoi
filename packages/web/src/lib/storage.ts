/**
 * LocalStorage persistence hook with SSR safety and error handling.
 * Always falls back to defaults — never crashes on bad/missing/outdated data.
 *
 * Hydration-safe: always initializes with `defaultValue` on both server and
 * client, then reads localStorage in a post-mount effect. This guarantees
 * SSR output matches the first client render (no FOUC / hydration mismatch).
 */

"use client";

import { useState, useEffect, useCallback, useRef } from "react";

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
  const hydrated = useRef(false);

  // Always start with defaultValue — matches SSR output, avoids hydration mismatch
  const [value, setValue] = useState<T>(defaultValue);

  // After mount: read localStorage and update via a scheduled state update
  useEffect(() => {
    if (hydrated.current) {
      return;
    }
    hydrated.current = true;
    const stored = readStoredValue(prefixedKey, defaultValue);
    if (stored !== undefined) {
      // Use requestAnimationFrame to avoid synchronous setState in effect
      requestAnimationFrame(() => {
        setValue(stored);
      });
    }
  }, [prefixedKey, defaultValue]);

  // Persist to localStorage whenever value changes (skip the initial default)
  useEffect(() => {
    if (!hydrated.current) {
      return;
    }
    try {
      window.localStorage.setItem(prefixedKey, JSON.stringify(value));
    } catch {
      // Storage full or blocked — silently ignore
    }
  }, [prefixedKey, value]);

  const setPersistedValue = useCallback(
    (valueOrUpdater: T | ((prev: T) => T)) => {
      // React's setValue handles function vs value discrimination internally
      setValue(valueOrUpdater);
    },
    [],
  );

  return [value, setPersistedValue];
}
