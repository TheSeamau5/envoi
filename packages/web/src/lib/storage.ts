/**
 * LocalStorage persistence hook with SSR safety and error handling.
 * Always falls back to defaults — never crashes on bad/missing/outdated data.
 */

"use client";

import { useState, useEffect, useCallback } from "react";

/** Prefix all envoi localStorage keys to avoid collisions */
const STORAGE_PREFIX = "envoi:";

/** React hook that persists state to localStorage */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, (valueOrUpdater: T | ((prev: T) => T)) => void] {
  const prefixedKey = `${STORAGE_PREFIX}${key}`;

  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return defaultValue;
    try {
      const stored = window.localStorage.getItem(prefixedKey);
      if (stored === null) return defaultValue;
      const parsed = JSON.parse(stored) as unknown;
      if (typeof parsed === typeof defaultValue) return parsed as T;
      return defaultValue;
    } catch {
      return defaultValue;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
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
