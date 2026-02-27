/**
 * Compare Client — main client component for the Compare page.
 * Manages mode toggle (Trace vs Setup), trace selection, tab navigation,
 * sorting, and model filtering.
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
import type { Trajectory, CompareMode, CompareTab } from "@/lib/types";
import { TRACE_COLORS, T } from "@/lib/tokens";
import { TOTAL_TESTS } from "@/lib/constants";
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

const TABS: { key: CompareTab; label: string; icon: typeof TrendingUp }[] = [
  { key: "curves", label: "Progress Curves", icon: TrendingUp },
  { key: "milestones", label: "Milestone Divergence", icon: Target },
  { key: "suites", label: "Suite Breakdown", icon: LayoutGrid },
];

export function CompareClient({ allTraces }: CompareClientProps) {
  const [mode, setMode] = useState<CompareMode>("traces");
  const [selectedIds, setSelectedIds] = useState<string[]>(() => {
    return allTraces.slice(0, 2).map((trace) => trace.id);
  });
  const [activeTab, setActiveTab] = useState<CompareTab>("curves");
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [modelFilter, setModelFilter] = useState<string>("all");
  const [focusedIndex, setFocusedIndex] = useState(0);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const focusedRowRef = useRef<HTMLDivElement>(null);

  /** Filter traces by model */
  const filteredTraces = useMemo(() => {
    if (modelFilter === "all") return allTraces;
    return allTraces.filter((trace) => trace.model === modelFilter);
  }, [allTraces, modelFilter]);

  /** Sort filtered traces */
  const sortedTraces = useMemo(() => {
    const sorted = [...filteredTraces];
    if (sortBy === "score") {
      sorted.sort((a, b) => b.finalPassed - a.finalPassed);
    } else {
      sorted.sort(
        (a, b) =>
          new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime(),
      );
    }
    return sorted;
  }, [filteredTraces, sortBy]);

  const selectedTraces = useMemo(
    () =>
      selectedIds
        .map((traceId) => allTraces.find((trace) => trace.id === traceId))
        .filter((trace): trace is Trajectory => trace !== undefined),
    [selectedIds, allTraces],
  );

  function toggleTrace(traceId: string) {
    setSelectedIds((prev) => {
      if (prev.includes(traceId)) {
        return prev.filter((existingId) => existingId !== traceId);
      }
      return [...prev, traceId];
    });
  }

  function getSelectionIndex(traceId: string): number {
    return selectedIds.indexOf(traceId);
  }

  /** Unique model names for the filter dropdown */
  const uniqueModels = useMemo(() => {
    const models = new Set(allTraces.map((t) => t.model));
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
          if (focused) toggleTrace(focused.id);
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
          (event.target as HTMLElement).blur();
          break;
      }
    },
    [sortedTraces, focusedIndex],
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Top bar: mode toggle + tab navigation */}
      <div className="flex h-[41px] shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        {/* Mode toggle */}
        <div className="flex items-center gap-[2px] rounded-[4px] border border-envoi-border p-[2px]">
          <button
            onClick={() => setMode("traces")}
            className="flex items-center gap-[6px] rounded-[3px] px-3 py-[5px] text-[10px] font-semibold transition-colors"
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
            className="flex items-center gap-[6px] rounded-[3px] px-3 py-[5px] text-[10px] font-semibold transition-colors"
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
                  className="flex items-center gap-[5px] rounded-[3px] px-3 py-[5px] text-[10px] font-medium transition-colors"
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
            className="flex w-[280px] shrink-0 flex-col border-r border-envoi-border outline-none"
            tabIndex={0}
            onKeyDown={handleSidebarKeyDown}
          >
            {/* Sidebar header */}
            <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[8px]">
              <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
                Trajectories ({sortedTraces.length})
              </span>
            </div>

            {/* Filter + sort row */}
            <div className="flex items-center gap-2 px-[14px] py-[6px]">
              {/* Model filter */}
              <Select value={modelFilter} onValueChange={setModelFilter}>
                <SelectTrigger
                  size="sm"
                  className="h-auto max-w-[140px] gap-1 rounded-[3px] border-none bg-transparent px-[6px] py-[3px] text-[9px] font-medium text-envoi-text-dim shadow-none hover:bg-envoi-border-light hover:text-envoi-text [&>span]:truncate"
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
                  <button className="flex shrink-0 items-center gap-[3px] rounded-[3px] px-[6px] py-[3px] text-[9px] font-medium text-envoi-text-dim transition-colors hover:bg-envoi-border-light hover:text-envoi-text">
                    <ArrowUpDown size={9} />
                    {sortBy === "score" ? "Score" : "Date"}
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="min-w-[100px] border-envoi-border bg-envoi-bg font-mono shadow-md"
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
                const selIndex = getSelectionIndex(trace.id);
                const isSelected = selIndex >= 0;
                const isFocused = traceIndex === focusedIndex;
                const color = isSelected
                  ? TRACE_COLORS[selIndex % TRACE_COLORS.length]!
                  : undefined;

                return (
                  <div
                    key={trace.id}
                    ref={isFocused ? focusedRowRef : undefined}
                    className="flex w-full items-center gap-[10px] border-b border-envoi-border-light px-[14px] py-[10px] text-left transition-colors hover:bg-envoi-surface"
                    style={{
                      borderLeft: isSelected
                        ? `3px solid ${color!.line}`
                        : "3px solid transparent",
                      background: isSelected ? color!.fill : undefined,
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
                      className="flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded-[3px] border"
                      style={{
                        borderColor: isSelected ? color!.line : T.border,
                        background: isSelected ? color!.line : "transparent",
                      }}
                    >
                      {isSelected && (
                        <Check size={10} color={T.bg} strokeWidth={3} />
                      )}
                    </span>

                    {/* Trace info */}
                    <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
                      <span className="truncate text-[11px] font-medium text-envoi-text">
                        {trace.id}
                      </span>
                      <span className="truncate text-[9px] text-envoi-text-dim">
                        {trace.model}
                      </span>
                      <span className="text-[9px] text-envoi-text-dim">
                        {formatDate(trace.startedAt)}
                      </span>
                    </div>

                    {/* Score */}
                    <div className="flex shrink-0 flex-col items-end gap-[2px]">
                      <span className="text-[11px] font-semibold text-envoi-text">
                        {trace.finalPassed}
                      </span>
                      <span className="text-[9px] text-envoi-text-dim">
                        {formatPercent(trace.finalPassed, TOTAL_TESTS)}
                      </span>
                    </div>

                    {/* Drill-down link */}
                    <Link
                      href={`/trajectory/${trace.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded text-envoi-text-dim transition-colors hover:bg-envoi-border hover:text-envoi-text"
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
            ) : (
              <>
                {activeTab === "curves" && (
                  <ProgressCurves traces={selectedTraces} />
                )}
                {activeTab === "milestones" && (
                  <MilestoneTable traces={selectedTraces} />
                )}
                {activeTab === "suites" && (
                  <SuiteBreakdown traces={selectedTraces} />
                )}
              </>
            )}
          </div>
        </div>
      ) : (
        <SetupCompare allTraces={allTraces} />
      )}
    </div>
  );
}
