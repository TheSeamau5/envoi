/**
 * Compare Context — shared state for all /compare sub-routes.
 * Manages trace selection, color assignment, full-data fetching,
 * sorting, filtering, and keyboard navigation.
 *
 * State persists across tab navigation because this provider
 * lives in the /compare layout.
 *
 * Full trajectory data is fetched via TanStack Query, keyed by
 * the sorted set of selected IDs. Selecting/deselecting a trace
 * changes the query key, and TanStack Query handles caching.
 */

"use client";

import {
  createContext,
  useContext,
  useState,
  useMemo,
  useCallback,
} from "react";
import type { ReactNode } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import type { Trajectory, Suite } from "@/lib/types";
import { computeTotalTests } from "@/lib/constants";
import { queryKeys } from "@/lib/query-keys";
import { usePersistedState } from "@/lib/storage";
import { useChatPageContext } from "@/lib/chat/use-chat-page-context";
import { useProjectRevision } from "@/lib/use-project-revision";

type SortKey = "score" | "date";

function normalizeTrajectories(value: unknown): Trajectory[] {
  if (Array.isArray(value)) {
    return value;
  }
  if (
    value !== null &&
    typeof value === "object" &&
    "trajectories" in value &&
    Array.isArray(value.trajectories)
  ) {
    return value.trajectories as Trajectory[];
  }
  return [];
}

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

/** Keep only valid color assignments (id present + non-negative index). */
function sanitizeColorMap(
  colorMap: Record<string, number>,
  validIds: Set<string>,
): Record<string, number> {
  const cleaned: Record<string, number> = {};
  for (const [id, colorIdx] of Object.entries(colorMap)) {
    if (typeof colorIdx !== "number" || colorIdx < 0) {
      continue;
    }
    if (validIds.size > 0 && !validIds.has(id)) {
      continue;
    }
    cleaned[id] = colorIdx;
  }
  return cleaned;
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

const CompareContext = createContext<CompareContextValue | undefined>(
  undefined,
);

type CompareProviderProps = {
  allTraces: Trajectory[];
  children: ReactNode;
  project: string;
};

export function CompareProvider({
  allTraces: initialAllTraces,
  children,
  project,
}: CompareProviderProps) {
  const serverAllTraces = useMemo(
    () => normalizeTrajectories(initialAllTraces),
    [initialAllTraces],
  );
  /**
   * colorMap: traceId → colorIndex.
   * Canonically persisted in localStorage via usePersistedState.
   */
  const [storedColorMap, setStoredColorMap] = usePersistedState<
    Record<string, number>
  >("compare-trace-colors", {});

  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [focusedIndex, setFocusedIndex] = useState(0);

  useProjectRevision(project, {
    invalidatePrefixes: [queryKeys.compare.all(project)],
  });

  /** Keep trajectory list fresh; if first SSR pass is empty, poll until data arrives. */
  const allTracesQuery = useQuery({
    queryKey: queryKeys.compare.all(project),
    queryFn: async () => {
      const response = await fetch(
        `/api/trajectories?project=${encodeURIComponent(project)}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectories");
      }
      const data: unknown = await response.json();
      return normalizeTrajectories(data);
    },
    initialData: serverAllTraces,
    staleTime: 0,
    refetchOnMount: true,
  });
  const allTraces = useMemo(
    () => normalizeTrajectories(allTracesQuery.data ?? serverAllTraces),
    [allTracesQuery.data, serverAllTraces],
  );
  const validTraceIds = useMemo(
    () => new Set(allTraces.map((trace) => trace.id)),
    [allTraces],
  );
  const colorMap = useMemo(
    () => sanitizeColorMap(storedColorMap, validTraceIds),
    [storedColorMap, validTraceIds],
  );

  /** Derive selected IDs from colorMap keys */
  const selectedIds = useMemo(() => Object.keys(colorMap), [colorMap]);
  const sortedSelectedIds = useMemo(
    () => [...selectedIds].sort(),
    [selectedIds],
  );

  useChatPageContext({
    page: "compare",
    project,
    selectedIds,
  });

  /**
   * Fetch full trajectory data for all selected IDs.
   * Key changes when selectedIds changes → auto-refetch.
   * keepPreviousData ensures deselecting a trace doesn't flash loading state.
   */
  const compareQuery = useQuery({
    queryKey: queryKeys.compare.byIds(project, sortedSelectedIds),
    queryFn: async () => {
      const response = await fetch(
        `/api/compare?project=${encodeURIComponent(project)}&ids=${sortedSelectedIds.join(",")}`,
      );
      if (!response.ok) {
        throw new Error("Failed to fetch trajectory data");
      }
      const data: unknown = await response.json();
      return normalizeTrajectories(data);
    },
    enabled: sortedSelectedIds.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 2 * 60 * 1000,
  });

  /** Derive fullTrajectories map from query data */
  const fullTrajectories: Record<string, Trajectory> = useMemo(() => {
    const map: Record<string, Trajectory> = {};
    for (const traj of normalizeTrajectories(compareQuery.data)) {
      map[traj.id] = traj;
    }
    return map;
  }, [compareQuery.data]);

  /** Whether any selected traces are still loading their full data */
  const isLoadingFull = compareQuery.isFetching;

  /** IDs currently being fetched — derive from query state */
  const loadingIds = useMemo<Set<string>>(() => {
    if (!compareQuery.isFetching) {
      return new Set();
    }
    // When fetching, all selected IDs that don't have full data yet are "loading"
    return new Set(selectedIds.filter((id) => !(id in fullTrajectories)));
  }, [compareQuery.isFetching, selectedIds, fullTrajectories]);

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
          new Date(traceB.startedAt).getTime() -
          new Date(traceA.startedAt).getTime(),
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
    return new Map(
      [...envMap.entries()].sort(([envA], [envB]) => envA.localeCompare(envB)),
    );
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
        .map(
          (traceId) =>
            fullTrajectories[traceId] ??
            allTraces.find((trace) => trace.id === traceId),
        )
        .filter((trace): trace is Trajectory => trace !== undefined),
    [selectedIds, allTraces, fullTrajectories],
  );

  /** Color indices parallel to selectedTraces */
  const colorIndices = useMemo(
    () => selectedTraces.map((trace) => colorMap[trace.id] ?? 0),
    [selectedTraces, colorMap],
  );

  /** Collect all unique suites from selected traces */
  const selectedSuites = useMemo(
    () => collectSuites(selectedTraces),
    [selectedTraces],
  );

  /**
   * Toggle trace selection — assigns min available color on select.
   * Changing colorMap triggers selectedIds change → query key change → auto-refetch.
   */
  const toggleTrace = useCallback(
    (traceId: string) => {
      setStoredColorMap((prev) => {
        const current = sanitizeColorMap(prev, validTraceIds);
        if (traceId in current) {
          const next = { ...current };
          delete next[traceId];
          return next;
        }
        const usedColors = new Set(Object.values(current));
        const newColor = minAvailableColor(usedColors);
        return { ...current, [traceId]: newColor };
      });
    },
    [setStoredColorMap, validTraceIds],
  );

  /** Get the assigned color index for a trace (-1 if not selected) */
  const getColorIndex = useCallback(
    (traceId: string): number => {
      return colorMap[traceId] ?? -1;
    },
    [colorMap],
  );

  /** Clear all selections */
  const clearSelection = useCallback(() => {
    setStoredColorMap({});
  }, [setStoredColorMap]);

  /** Unique model names for the filter dropdown */
  const uniqueModels = useMemo(() => {
    const models = new Set(allTraces.map((trace) => trace.model));
    return [...models];
  }, [allTraces]);

  /** Clamp focused index — derived value, not an effect */
  const clampedFocusedIndex = useMemo(
    () =>
      flatSortedTraces.length === 0
        ? 0
        : Math.min(focusedIndex, flatSortedTraces.length - 1),
    [focusedIndex, flatSortedTraces.length],
  );

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
          setFocusedIndex((prev) =>
            Math.min(flatSortedTraces.length - 1, prev + 1),
          );
          break;
        case " ":
        case "Enter": {
          event.preventDefault();
          const focused = flatSortedTraces[clampedFocusedIndex];
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
    [flatSortedTraces, clampedFocusedIndex, toggleTrace],
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
      focusedIndex: clampedFocusedIndex,
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
      clampedFocusedIndex,
      handleSidebarKeyDown,
      selectedIds,
      loadingIds,
      computeTraceTotal,
    ],
  );

  return (
    <CompareContext.Provider value={value}>{children}</CompareContext.Provider>
  );
}

export function useCompare(): CompareContextValue {
  const context = useContext(CompareContext);
  if (context === undefined) {
    throw new Error("useCompare must be used within a CompareProvider");
  }
  return context;
}
