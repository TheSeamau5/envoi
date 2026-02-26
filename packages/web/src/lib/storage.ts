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

/** React hook that persists state to localStorage */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, (valueOrUpdater: T | ((prev: T) => T)) => void] {
  const prefixedKey = `${STORAGE_PREFIX}${key}`;

  // Always start with defaultValue — matches SSR output, avoids hydration mismatch
  const [value, setValue] = useState<T>(defaultValue);
  const hydrated = useRef(false);

  // After mount: read localStorage and update if a stored value exists
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    try {
      const stored = window.localStorage.getItem(prefixedKey);
      if (stored === null) return;
      const parsed = JSON.parse(stored) as unknown;
      if (typeof parsed === typeof defaultValue) {
        setValue(parsed as T);
      }
    } catch {
      // Bad data — keep default
    }
  }, [prefixedKey, defaultValue]);

  // Persist to localStorage whenever value changes (skip the initial default)
  useEffect(() => {
    if (!hydrated.current) return;
    try {
      window.localStorage.setItem(prefixedKey, JSON.stringify(value));
    } catch {
      // Storage full or blocked — silently ignore
    }
  }, [prefixedKey, value]);

  const setPersistedValue = useCallback(
    (valueOrUpdater: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const next =
          typeof valueOrUpdater === "function"
            ? (valueOrUpdater as (prev: T) => T)(prev)
            : valueOrUpdater;
        return next;
      });
    },
    [],
  );

  return [value, setPersistedValue];
}
