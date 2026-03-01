/**
 * Compare Client — main client component for the Compare page.
 * Manages mode toggle (Trace vs Setup), trace selection, tab navigation,
 * sorting, and model filtering.
 *
 * Sidebar uses lightweight summary data (passed via props from server component).
 * When traces are selected for comparison, their full trajectory data (with
 * commit histories) is fetched from /api/compare?ids=... so that progress
 * curves and other visualizations render correctly against S3-backed data.
 *
 * Trace colors are stable: each trace is assigned a color index on selection
 * and keeps it even when other traces are deselected. Assignments are persisted
 * to localStorage with graceful error handling for stale/missing trace IDs.
 */

"use client";

import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import {
  GitCompareArrows,
  Settings2,
  Check,
  TrendingUp,
  Target,
  LayoutGrid,
  ArrowUpRight,
  ArrowUpDown,
} from "lucide-react";
import Link from "next/link";
import type { Trajectory, CompareMode, CompareTab, Suite } from "@/lib/types";
import { TRACE_COLORS, T } from "@/lib/tokens";
import { SUITES as DEFAULT_SUITES, computeTotalTests } from "@/lib/constants";
import { formatPercent, formatDate } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ProgressCurves } from "./progress-curves";
import { MilestoneTable } from "./milestone-table";
import { SuiteBreakdown } from "./suite-breakdown";
import { SetupCompare } from "./setup-compare";

type CompareClientProps = {
  allTraces: Trajectory[];
};

type SortKey = "score" | "date";

const STORAGE_KEY = "envoi:compare-trace-colors";

const TABS: { key: CompareTab; label: string; icon: typeof TrendingUp }[] = [
  { key: "curves", label: "Progress Curves", icon: TrendingUp },
  { key: "milestones", label: "Milestone Divergence", icon: Target },
  { key: "suites", label: "Suite Breakdown", icon: LayoutGrid },
];

/** Find the smallest non-negative integer not in `usedSet` */
function minAvailableColor(usedSet: Set<number>): number {
  let colorIndex = 0;
  while (usedSet.has(colorIndex)) {
    colorIndex++;
  }
  return colorIndex;
}

export function CompareClient({ allTraces }: CompareClientProps) {
  // Derive suites from data, fallback to defaults
  const suites: Suite[] = allTraces[0]?.suites ?? DEFAULT_SUITES;
  const totalTests = computeTotalTests(suites);

  const [mode, setMode] = useState<CompareMode>("traces");

  /**
   * Full trajectory data keyed by trace ID, fetched on demand when selected.
   * Summary data from allTraces is sufficient for the sidebar; full data
   * (with commit histories) is needed for progress curves and other tabs.
   */
  const [fullTrajectories, setFullTrajectories] = useState<Record<string, Trajectory>>({});
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());

  /**
   * All trajectories with full commit data for Setup Compare mode.
   * Fetched once when the user switches to "setups" mode.
   */
  const [allFullTraces, setAllFullTraces] = useState<Trajectory[]>([]);
  const [loadingAllFull, setLoadingAllFull] = useState(false);
  const allFullFetched = useRef(false);

  /**
   * colorMap: traceId → colorIndex.
   * Keys are the selected trace IDs; values are their assigned color indices.
   * Initialized with the first 2 traces; hydrated from localStorage on mount.
   */
  const [colorMap, setColorMap] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    allTraces.slice(0, 2).forEach((trace, traceIndex) => {
      initial[trace.id] = traceIndex;
    });
    return initial;
  });

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

  const [activeTab, setActiveTab] = useState<CompareTab>("curves");
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [focusedIndex, setFocusedIndex] = useState(0);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const focusedRowRef = useRef<HTMLDivElement>(null);

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
        // On error, silently ignore — traces will show as loading or use summary fallback
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

  /** Fetch all full trajectories when entering Setup Compare mode */
  useEffect(() => {
    if (mode !== "setups" || allFullFetched.current || loadingAllFull) {
      return;
    }

    setLoadingAllFull(true);
    fetch("/api/compare")
      .then((res) => res.json())
      .then((data: Trajectory[]) => {
        setAllFullTraces(data);
        allFullFetched.current = true;
        // Also populate fullTrajectories cache for trace mode
        setFullTrajectories((prev) => {
          const next = { ...prev };
          for (const traj of data) {
            next[traj.id] = traj;
          }
          return next;
        });
      })
      .catch(() => {
        // On error, fall back to summary data
      })
      .finally(() => setLoadingAllFull(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  /** Filter traces by model */
  const filteredTraces = useMemo(() => {
    if (modelFilter === "all") {
      return allTraces;
    }
    return allTraces.filter((trace) => trace.model === modelFilter);
  }, [allTraces, modelFilter]);

  /** Sort filtered traces */
  const sortedTraces = useMemo(() => {
    const sorted = [...filteredTraces];
    if (sortBy === "score") {
      sorted.sort((traceA, traceB) => traceB.finalPassed - traceA.finalPassed);
    } else {
      sorted.sort(
        (traceA, traceB) =>
          new Date(traceB.startedAt).getTime() - new Date(traceA.startedAt).getTime(),
      );
    }
    return sorted;
  }, [filteredTraces, sortBy]);

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

  /** Toggle trace selection — assigns min available color on select */
  const toggleTrace = useCallback((traceId: string) => {
    setColorMap((prev) => {
      if (traceId in prev) {
        // Deselect: remove from map, other colors are untouched
        const next = { ...prev };
        delete next[traceId];
        return next;
      }
      // Select: pick the smallest unused color index
      const usedColors = new Set(Object.values(prev));
      const newColor = minAvailableColor(usedColors);
      return { ...prev, [traceId]: newColor };
    });
  }, []);

  /** Get the assigned color index for a trace (-1 if not selected) */
  function getColorIndex(traceId: string): number {
    return colorMap[traceId] ?? -1;
  }

  /** Unique model names for the filter dropdown */
  const uniqueModels = useMemo(() => {
    const models = new Set(allTraces.map((trace) => trace.model));
    return [...models];
  }, [allTraces]);

  /** Clamp focused index when filtered list changes */
  useEffect(() => {
    if (focusedIndex >= sortedTraces.length) {
      setFocusedIndex(Math.max(0, sortedTraces.length - 1));
    }
  }, [sortedTraces.length, focusedIndex]);

  /** Scroll focused row into view */
  useEffect(() => {
    focusedRowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedIndex]);

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
          setFocusedIndex((prev) => Math.min(sortedTraces.length - 1, prev + 1));
          break;
        case " ":
        case "Enter": {
          event.preventDefault();
          const focused = sortedTraces[focusedIndex];
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
          setFocusedIndex(Math.max(0, sortedTraces.length - 1));
          break;
        case "Escape":
          if (event.target instanceof HTMLElement) {
            event.target.blur();
          }
          break;
      }
    },
    [sortedTraces, focusedIndex, toggleTrace],
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Top bar: mode toggle + tab navigation */}
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        {/* Mode toggle */}
        <div className="flex items-center gap-0.5 rounded-sm border border-envoi-border p-0.5">
          <button
            onClick={() => setMode("traces")}
            className="flex items-center gap-1.5 rounded-[3px] px-3 py-1.25 text-[10px] font-semibold transition-colors"
            style={{
              background: mode === "traces" ? T.text : "transparent",
              color: mode === "traces" ? T.bg : T.textMuted,
            }}
          >
            <GitCompareArrows size={12} />
            Trace Compare
          </button>
          <button
            onClick={() => setMode("setups")}
            className="flex items-center gap-1.5 rounded-[3px] px-3 py-1.25 text-[10px] font-semibold transition-colors"
            style={{
              background: mode === "setups" ? T.text : "transparent",
              color: mode === "setups" ? T.bg : T.textMuted,
            }}
          >
            <Settings2 size={12} />
            Setup Compare
          </button>
        </div>

        {/* Tab navigation (only in trace mode) */}
        {mode === "traces" && (
          <div className="ml-6 flex items-center gap-1">
            {TABS.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className="flex items-center gap-1.25 rounded-[3px] px-3 py-1.25 text-[10px] font-medium transition-colors"
                  style={{
                    background: isActive ? T.accentBg : "transparent",
                    color: isActive ? T.accentDark : T.textMuted,
                  }}
                >
                  <Icon size={11} />
                  {tab.label}
                </button>
              );
            })}
          </div>
        )}

        {/* Selection count badge (trace mode) */}
        {mode === "traces" && (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[10px] text-envoi-text-dim">
              {selectedTraces.length} selected
            </span>
          </div>
        )}
      </div>

      {/* Body */}
      {mode === "traces" ? (
        <div className="flex flex-1 overflow-hidden">
          {/* Trace selection sidebar */}
          <div
            ref={sidebarRef}
            className="flex w-70 shrink-0 flex-col border-r border-envoi-border outline-none"
            tabIndex={0}
            onKeyDown={handleSidebarKeyDown}
          >
            {/* Sidebar header */}
            <div className="flex items-center justify-between border-b border-envoi-border bg-envoi-surface px-3.5 py-2">
              <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                Trajectories ({sortedTraces.length})
              </span>
              {selectedIds.length > 0 && (
                <button
                  onClick={() => setColorMap({})}
                  className="text-[10px] text-envoi-text-dim transition-colors hover:text-envoi-text"
                >
                  Deselect all
                </button>
              )}
            </div>

            {/* Filter + sort row */}
            <div className="flex items-center gap-2 px-3.5 py-1.5">
              {/* Model filter */}
              <Select value={modelFilter} onValueChange={setModelFilter}>
                <SelectTrigger
                  size="sm"
                  className="h-auto max-w-35 gap-1 rounded-[3px] border-none bg-transparent px-1.5 py-0.75 text-[9px] font-medium text-envoi-text-dim shadow-none hover:bg-envoi-border-light hover:text-envoi-text [&>span]:truncate"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent
                  position="popper"
                  className="border-envoi-border bg-envoi-bg font-mono shadow-md"
                >
                  <SelectItem
                    value="all"
                    className="text-[10px] text-envoi-text focus:bg-envoi-surface"
                  >
                    All models
                  </SelectItem>
                  {uniqueModels.map((model) => (
                    <SelectItem
                      key={model}
                      value={model}
                      className="text-[10px] text-envoi-text focus:bg-envoi-surface"
                    >
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <div className="flex-1" />

              {/* Sort dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="flex shrink-0 items-center gap-0.75 rounded-[3px] px-1.5 py-0.75 text-[9px] font-medium text-envoi-text-dim transition-colors hover:bg-envoi-border-light hover:text-envoi-text">
                    <ArrowUpDown size={9} />
                    {sortBy === "score" ? "Score" : "Date"}
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="min-w-25 border-envoi-border bg-envoi-bg font-mono shadow-md"
                >
                  <DropdownMenuItem
                    onClick={() => setSortBy("score")}
                    className="text-[10px] text-envoi-text focus:bg-envoi-surface"
                    style={{
                      fontWeight: sortBy === "score" ? 600 : 400,
                      color: sortBy === "score" ? T.accent : T.text,
                    }}
                  >
                    Score
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => setSortBy("date")}
                    className="text-[10px] text-envoi-text focus:bg-envoi-surface"
                    style={{
                      fontWeight: sortBy === "date" ? 600 : 400,
                      color: sortBy === "date" ? T.accent : T.text,
                    }}
                  >
                    Date
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Trace list */}
            <div className="flex-1 overflow-y-auto">
              {sortedTraces.map((trace, traceIndex) => {
                const colorIdx = getColorIndex(trace.id);
                const isSelected = colorIdx >= 0;
                const isFocused = traceIndex === focusedIndex;
                const color = isSelected
                  ? TRACE_COLORS[colorIdx % TRACE_COLORS.length]
                  : undefined;

                return (
                  <div
                    key={trace.id}
                    ref={isFocused ? focusedRowRef : undefined}
                    className="flex w-full items-center gap-2.5 border-b border-envoi-border-light px-3.5 py-2.5 text-left transition-colors hover:bg-envoi-surface"
                    style={{
                      borderLeft: color
                        ? `3px solid ${color.line}`
                        : "3px solid transparent",
                      background: color?.fill,
                      cursor: "pointer",
                      outline: isFocused ? `2px solid ${T.accent}` : "none",
                      outlineOffset: -2,
                    }}
                    onClick={() => {
                      setFocusedIndex(traceIndex);
                      toggleTrace(trace.id);
                    }}
                  >
                    {/* Checkbox */}
                    <span
                      className="flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border"
                      style={{
                        borderColor: color ? color.line : T.border,
                        background: color ? color.line : "transparent",
                      }}
                    >
                      {isSelected && (
                        <Check size={10} color={T.bg} strokeWidth={3} />
                      )}
                    </span>

                    {/* Trace info */}
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate text-[11px] font-medium text-envoi-text">
                        {trace.id}
                      </span>
                      <span className="truncate text-[9px] text-envoi-text-dim">
                        {trace.model}
                      </span>
                      <span className="text-[9px] text-envoi-text-dim">
                        {formatDate(trace.startedAt)}
                      </span>
                      {/* Score bar */}
                      <div className="mt-px h-0.75 w-full rounded-full bg-envoi-border-light">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${(trace.finalPassed / totalTests) * 100}%`,
                            background: isSelected && color ? color.line : T.textDim,
                          }}
                        />
                      </div>
                    </div>

                    {/* Score */}
                    <div className="flex shrink-0 flex-col items-end gap-0.5">
                      <span className="text-[11px] font-semibold text-envoi-text">
                        {trace.finalPassed}
                      </span>
                      <span className="text-[9px] text-envoi-text-dim">
                        {formatPercent(trace.finalPassed, totalTests)}
                      </span>
                    </div>

                    {/* Drill-down link */}
                    <Link
                      href={`/trajectory/${trace.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="flex h-5.5 w-5.5 shrink-0 items-center justify-center rounded text-envoi-text-dim transition-colors hover:bg-envoi-border hover:text-envoi-text"
                    >
                      <ArrowUpRight size={12} />
                    </Link>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Main content area */}
          <div className="flex-1 overflow-y-auto p-4">
            {selectedTraces.length === 0 ? (
              <div className="flex h-full items-center justify-center">
                <div className="flex flex-col items-center gap-2">
                  <GitCompareArrows size={24} className="text-envoi-text-dim" />
                  <span className="text-[12px] text-envoi-text-muted">
                    Select traces to compare
                  </span>
                </div>
              </div>
            ) : isLoadingFull ? (
              <div className="flex h-full items-center justify-center">
                <span className="text-[12px] text-envoi-text-muted">
                  Loading trajectory data...
                </span>
              </div>
            ) : (
              <>
                {activeTab === "curves" && (
                  <ProgressCurves traces={selectedTraces} colorIndices={colorIndices} />
                )}
                {activeTab === "milestones" && (
                  <MilestoneTable traces={selectedTraces} colorIndices={colorIndices} suites={suites} />
                )}
                {activeTab === "suites" && (
                  <SuiteBreakdown traces={selectedTraces} colorIndices={colorIndices} suites={suites} />
                )}
              </>
            )}
          </div>
        </div>
      ) : loadingAllFull ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="text-[12px] text-envoi-text-muted">
            Loading trajectory data...
          </span>
        </div>
      ) : (
        <SetupCompare allTraces={allFullTraces.length > 0 ? allFullTraces : allTraces} suites={suites} totalTests={totalTests} />
      )}
    </div>
  );
}
