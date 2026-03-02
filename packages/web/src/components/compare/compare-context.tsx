/**
 * Compare Context — shared state for all /compare sub-routes.
 * Manages trace selection, color assignment, full-data fetching,
 * sorting, filtering, and keyboard navigation.
 *
 * State persists across tab navigation because this provider
 * lives in the /compare layout.
 */

"use client";

import {
  createContext,
  useContext,
  useState,
  useMemo,
  useCallback,
  useEffect,
} from "react";
import type { ReactNode } from "react";
import type { Trajectory, Suite } from "@/lib/types";
import { computeTotalTests } from "@/lib/constants";

type SortKey = "score" | "date";

const STORAGE_KEY = "envoi:compare-trace-colors";

/** Find the smallest non-negative integer not in `usedSet` */
function minAvailableColor(usedSet: Set<number>): number {
  let colorIndex = 0;
  while (usedSet.has(colorIndex)) {
    colorIndex++;
  }
  return colorIndex;
}

/** Collect all unique suites from selected traces */
function collectSuites(traces: Trajectory[]): Suite[] {
  const suiteMap = new Map<string, number>();
  for (const trace of traces) {
    if (trace.suites) {
      for (const suite of trace.suites) {
        const existing = suiteMap.get(suite.name);
        if (existing === undefined || suite.total > existing) {
          suiteMap.set(suite.name, suite.total);
        }
      }
    }
  }
  return [...suiteMap.entries()]
    .map(([name, total]) => ({ name, total }))
    .sort((suiteA, suiteB) => suiteA.name.localeCompare(suiteB.name));
}

type CompareContextValue = {
  allTraces: Trajectory[];
  selectedTraces: Trajectory[];
  colorIndices: number[];
  selectedSuites: Suite[];
  isLoadingFull: boolean;
  colorMap: Record<string, number>;
  toggleTrace: (id: string) => void;
  getColorIndex: (id: string) => number;
  clearSelection: () => void;
  sortBy: SortKey;
  setSortBy: (key: SortKey) => void;
  modelFilter: string;
  setModelFilter: (model: string) => void;
  uniqueModels: string[];
  sidebarGroups: Map<string, Trajectory[]>;
  flatSortedTraces: Trajectory[];
  focusedIndex: number;
  setFocusedIndex: (index: number) => void;
  handleSidebarKeyDown: (event: React.KeyboardEvent) => void;
  selectedIds: string[];
  loadingIds: Set<string>;
  computeTraceTotal: (trace: Trajectory) => number;
};

const CompareContext = createContext<CompareContextValue | undefined>(undefined);

type CompareProviderProps = {
  allTraces: Trajectory[];
  children: ReactNode;
};

export function CompareProvider({ allTraces, children }: CompareProviderProps) {
  /**
   * Full trajectory data keyed by trace ID, fetched on demand when selected.
   * Summary data from allTraces is sufficient for the sidebar; full data
   * (with commit histories) is needed for progress curves and other tabs.
   */
  const [fullTrajectories, setFullTrajectories] = useState<Record<string, Trajectory>>({});
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());

  /**
   * colorMap: traceId → colorIndex.
   * Keys are the selected trace IDs; values are their assigned color indices.
   * Starts empty; hydrated from localStorage on mount.
   */
  const [colorMap, setColorMap] = useState<Record<string, number>>({});

  /** Hydrate from localStorage (once, on mount) */
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (!stored) {
        return;
      }
      const parsed: unknown = JSON.parse(stored);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return;
      }
      const validIds = new Set(allTraces.map((trace) => trace.id));
      const cleaned: Record<string, number> = {};
      for (const [id, colorIdx] of Object.entries(parsed)) {
        if (validIds.has(id) && typeof colorIdx === "number" && colorIdx >= 0) {
          cleaned[id] = colorIdx;
        }
      }
      if (Object.keys(cleaned).length > 0) {
        setColorMap(cleaned);
      }
    } catch {
      // Bad data — keep default
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Persist colorMap to localStorage on every change */
  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(colorMap));
    } catch {
      // Storage full or blocked — silently ignore
    }
  }, [colorMap]);

  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [focusedIndex, setFocusedIndex] = useState(0);

  /** Derive selected IDs from colorMap keys */
  const selectedIds = useMemo(() => Object.keys(colorMap), [colorMap]);

  /** Fetch full trajectory data for newly selected traces */
  useEffect(() => {
    const idsToFetch = selectedIds.filter(
      (id) => !fullTrajectories[id] && !loadingIds.has(id),
    );
    if (idsToFetch.length === 0) {
      return;
    }

    setLoadingIds((prev) => {
      const next = new Set(prev);
      for (const id of idsToFetch) {
        next.add(id);
      }
      return next;
    });

    fetch(`/api/compare?ids=${idsToFetch.join(",")}`)
      .then((res) => res.json())
      .then((data: Trajectory[]) => {
        setFullTrajectories((prev) => {
          const next = { ...prev };
          for (const traj of data) {
            next[traj.id] = traj;
          }
          return next;
        });
      })
      .catch(() => {
        // On error, silently ignore
      })
      .finally(() => {
        setLoadingIds((prev) => {
          const next = new Set(prev);
          for (const id of idsToFetch) {
            next.delete(id);
          }
          return next;
        });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIds]);

  /** Filter traces by model */
  const filteredTraces = useMemo(() => {
    if (modelFilter === "all") {
      return allTraces;
    }
    return allTraces.filter((trace) => trace.model === modelFilter);
  }, [allTraces, modelFilter]);

  /** Group filtered + sorted traces by environment for sidebar rendering */
  const sidebarGroups = useMemo(() => {
    const sorted = [...filteredTraces];
    if (sortBy === "score") {
      sorted.sort((traceA, traceB) => traceB.finalPassed - traceA.finalPassed);
    } else {
      sorted.sort(
        (traceA, traceB) =>
          new Date(traceB.startedAt).getTime() - new Date(traceA.startedAt).getTime(),
      );
    }

    const envMap = new Map<string, Trajectory[]>();
    for (const trace of sorted) {
      const env = trace.environment || "unknown";
      const existing = envMap.get(env);
      if (existing) {
        existing.push(trace);
      } else {
        envMap.set(env, [trace]);
      }
    }
    return new Map([...envMap.entries()].sort(([envA], [envB]) => envA.localeCompare(envB)));
  }, [filteredTraces, sortBy]);

  /** Flat sorted list for keyboard navigation */
  const flatSortedTraces = useMemo(() => {
    const result: Trajectory[] = [];
    for (const traces of sidebarGroups.values()) {
      result.push(...traces);
    }
    return result;
  }, [sidebarGroups]);

  /** Selected traces for visualizations — uses full trajectory data when available */
  const selectedTraces = useMemo(
    () =>
      selectedIds
        .map((traceId) => fullTrajectories[traceId] ?? allTraces.find((trace) => trace.id === traceId))
        .filter((trace): trace is Trajectory => trace !== undefined),
    [selectedIds, allTraces, fullTrajectories],
  );

  /** Whether any selected traces are still loading their full data */
  const isLoadingFull = loadingIds.size > 0;

  /** Color indices parallel to selectedTraces */
  const colorIndices = useMemo(
    () => selectedTraces.map((trace) => colorMap[trace.id] ?? 0),
    [selectedTraces, colorMap],
  );

  /** Collect all unique suites from selected traces */
  const selectedSuites = useMemo(() => collectSuites(selectedTraces), [selectedTraces]);

  /** Toggle trace selection — assigns min available color on select */
  const toggleTrace = useCallback((traceId: string) => {
    setColorMap((prev) => {
      if (traceId in prev) {
        const next = { ...prev };
        delete next[traceId];
        return next;
      }
      const usedColors = new Set(Object.values(prev));
      const newColor = minAvailableColor(usedColors);
      return { ...prev, [traceId]: newColor };
    });
  }, []);

  /** Get the assigned color index for a trace (-1 if not selected) */
  const getColorIndex = useCallback(
    (traceId: string): number => {
      return colorMap[traceId] ?? -1;
    },
    [colorMap],
  );

  /** Clear all selections */
  const clearSelection = useCallback(() => {
    setColorMap({});
  }, []);

  /** Unique model names for the filter dropdown */
  const uniqueModels = useMemo(() => {
    const models = new Set(allTraces.map((trace) => trace.model));
    return [...models];
  }, [allTraces]);

  /** Clamp focused index when filtered list changes */
  useEffect(() => {
    if (focusedIndex >= flatSortedTraces.length) {
      setFocusedIndex(Math.max(0, flatSortedTraces.length - 1));
    }
  }, [flatSortedTraces.length, focusedIndex]);

  /** Compute total tests for a trace */
  const computeTraceTotal = useCallback((trace: Trajectory): number => {
    return trace.suites ? computeTotalTests(trace.suites) : trace.totalTests;
  }, []);

  /** Keyboard handler for sidebar navigation */
  const handleSidebarKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      switch (event.key) {
        case "ArrowUp":
        case "k":
          event.preventDefault();
          setFocusedIndex((prev) => Math.max(0, prev - 1));
          break;
        case "ArrowDown":
        case "j":
          event.preventDefault();
          setFocusedIndex((prev) => Math.min(flatSortedTraces.length - 1, prev + 1));
          break;
        case " ":
        case "Enter": {
          event.preventDefault();
          const focused = flatSortedTraces[focusedIndex];
          if (focused) {
            toggleTrace(focused.id);
          }
          break;
        }
        case "Home":
          event.preventDefault();
          setFocusedIndex(0);
          break;
        case "End":
          event.preventDefault();
          setFocusedIndex(Math.max(0, flatSortedTraces.length - 1));
          break;
        case "Escape":
          if (event.target instanceof HTMLElement) {
            event.target.blur();
          }
          break;
      }
    },
    [flatSortedTraces, focusedIndex, toggleTrace],
  );

  const value = useMemo<CompareContextValue>(
    () => ({
      allTraces,
      selectedTraces,
      colorIndices,
      selectedSuites,
      isLoadingFull,
      colorMap,
      toggleTrace,
      getColorIndex,
      clearSelection,
      sortBy,
      setSortBy,
      modelFilter,
      setModelFilter,
      uniqueModels,
      sidebarGroups,
      flatSortedTraces,
      focusedIndex,
      setFocusedIndex,
      handleSidebarKeyDown,
      selectedIds,
      loadingIds,
      computeTraceTotal,
    }),
    [
      allTraces,
      selectedTraces,
      colorIndices,
      selectedSuites,
      isLoadingFull,
      colorMap,
      toggleTrace,
      getColorIndex,
      clearSelection,
      sortBy,
      modelFilter,
      uniqueModels,
      sidebarGroups,
      flatSortedTraces,
      focusedIndex,
      handleSidebarKeyDown,
      selectedIds,
      loadingIds,
      computeTraceTotal,
    ],
  );

  return (
    <CompareContext.Provider value={value}>
      {children}
    </CompareContext.Provider>
  );
}

export function useCompare(): CompareContextValue {
  const context = useContext(CompareContext);
  if (context === undefined) {
    throw new Error("useCompare must be used within a CompareProvider");
  }
  return context;
}
