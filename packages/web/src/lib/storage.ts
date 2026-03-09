/**
 * Canonical localStorage-backed state hook.
 *
 * - Reads localStorage in the state initializer (before first client render).
 * - Supports a server-provided initial value for SSR alignment.
 * - Syncs same-tab and cross-tab updates via storage/custom events.
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const STORAGE_PREFIX = "envoi:";
const STORAGE_EVENT = "envoi-storage";

type PersistedStateOptions<T> = {
  /**
   * Server-provided initial value (for SSR/client alignment).
   * LocalStorage still takes precedence when present.
   */
  initialValue?: T;
  /**
   * Optional side effect on writes (for cookie mirroring, telemetry, etc).
   */
  onChange?: (value: T) => void;
};

/** Type guard: validates that runtime typeof matches the sample value. */
function isSameType<T>(value: unknown, sample: T): value is T {
  if (Array.isArray(sample)) {
    return Array.isArray(value);
  }
  if (sample !== null && typeof sample === "object") {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }
  if (Array.isArray(value)) {
    return false;
  }
  return typeof value === typeof sample;
}

/** Read a stored value from localStorage with type-compat fallback. */
function readStoredValue<T>(
  prefixedKey: string,
  defaultValue: T,
): T | undefined {
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

/** Resolve the value for first render and storage refreshes. */
function resolveValue<T>(
  prefixedKey: string,
  defaultValue: T,
  initialValue?: T,
): T {
  const stored = readStoredValue(prefixedKey, defaultValue);
  if (stored !== undefined) {
    return stored;
  }
  if (initialValue !== undefined) {
    return initialValue;
  }
  return defaultValue;
}

/**
 * Persisted state hook.
 * LocalStorage is read before the first client render via lazy initializer.
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
  options?: PersistedStateOptions<T>,
): [T, (valueOrUpdater: T | ((prev: T) => T)) => void] {
  const prefixedKey = useMemo(() => `${STORAGE_PREFIX}${key}`, [key]);
  const initialValue = options?.initialValue;
  const onChange = options?.onChange;

  const [value, setValue] = useState<T>(() =>
    resolveValue(prefixedKey, defaultValue, initialValue),
  );
  const valueRef = useRef(value);

  const refreshFromStorage = useCallback(() => {
    setValue((prev) => {
      const next = resolveValue(prefixedKey, defaultValue, initialValue);
      valueRef.current = next;
      return Object.is(prev, next) ? prev : next;
    });
  }, [prefixedKey, defaultValue, initialValue]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handleStorage = (event: StorageEvent): void => {
      if (event.storageArea !== window.localStorage) {
        return;
      }
      if (event.key !== prefixedKey) {
        return;
      }
      refreshFromStorage();
    };

    const handleCustomEvent = (event: Event): void => {
      const customEvent = event as CustomEvent<{ key?: string }>;
      const changedKey = customEvent.detail?.key;
      if (changedKey && changedKey !== prefixedKey) {
        return;
      }
      refreshFromStorage();
    };

    window.addEventListener("storage", handleStorage);
    window.addEventListener(STORAGE_EVENT, handleCustomEvent);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener(STORAGE_EVENT, handleCustomEvent);
    };
  }, [prefixedKey, refreshFromStorage]);

  useEffect(() => {
    onChange?.(value);
  }, [onChange, value]);

  const setPersistedValue = useCallback(
    (valueOrUpdater: T | ((prev: T) => T)) => {
      const previous = valueRef.current;
      const next =
        typeof valueOrUpdater === "function"
          ? (valueOrUpdater as (previous: T) => T)(previous)
          : valueOrUpdater;

      valueRef.current = next;
      setValue(next);

      if (typeof window !== "undefined") {
        try {
          window.localStorage.setItem(prefixedKey, JSON.stringify(next));
        } catch {
          // Storage full or blocked — keep in-memory state anyway.
        }
        window.dispatchEvent(
          new CustomEvent(STORAGE_EVENT, { detail: { key: prefixedKey } }),
        );
      }
    },
    [prefixedKey],
  );

  return [value, setPersistedValue];
}
