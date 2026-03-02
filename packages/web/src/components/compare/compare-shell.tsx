/**
 * Compare Shell — persistent layout for /compare sub-routes.
 * Renders the tab bar (top) and trace selection sidebar (left)
 * with a content area (right) for the active sub-route.
 *
 * Tab navigation uses Link components with pathname-based active detection.
 * The sidebar and all selection state persist across tab navigation
 * because this component lives in the /compare layout.
 */

"use client";

import { useRef, useEffect, useMemo } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  GitCompareArrows,
  Check,
  TrendingUp,
  Target,
  LayoutGrid,
  ArrowUpRight,
  ArrowUpDown,
} from "lucide-react";
import type { Trajectory } from "@/lib/types";
import { TRACE_COLORS, T } from "@/lib/tokens";
import { formatPercent, formatDate, needsYear } from "@/lib/utils";
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
import { useCompare } from "./compare-context";

const TABS = [
  { href: "/compare/curves", label: "Progress Curves", icon: TrendingUp },
  { href: "/compare/milestones", label: "Milestone Divergence", icon: Target },
  { href: "/compare/suites", label: "Suite Breakdown", icon: LayoutGrid },
] as const;

type CompareShellProps = {
  children: ReactNode;
};

export function CompareShell({ children }: CompareShellProps) {
  const pathname = usePathname();
  const {
    allTraces,
    selectedTraces,
    selectedIds,
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
    toggleTrace,
    getColorIndex,
    clearSelection,
    isLoadingFull,
    computeTraceTotal,
  } = useCompare();

  const sidebarRef = useRef<HTMLDivElement>(null);
  const focusedRowRef = useRef<HTMLDivElement>(null);
  const showYear = useMemo(() => needsYear(allTraces.map((trace) => trace.startedAt)), [allTraces]);

  /** Scroll focused row into view */
  useEffect(() => {
    focusedRowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedIndex]);

  /** Track flat index across environment groups */
  let flatIndex = 0;

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Top bar: tab navigation */}
      <div className="flex h-10.25 shrink-0 items-center border-b border-envoi-border bg-envoi-bg px-4">
        <div className="flex items-center gap-1">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = pathname === tab.href;
            return (
              <Link
                key={tab.href}
                href={tab.href}
                className="flex items-center gap-1.25 rounded-[3px] px-3 py-1.25 text-[12px] font-medium transition-colors"
                style={{
                  background: isActive ? T.accentBg : "transparent",
                  color: isActive ? T.accentDark : T.textMuted,
                }}
              >
                <Icon size={11} />
                {tab.label}
              </Link>
            );
          })}
        </div>

        {/* Selection count badge */}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[12px] text-envoi-text-dim">
            {selectedTraces.length} selected
          </span>
        </div>
      </div>

      {/* Body: sidebar + content */}
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
            <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Trajectories ({flatSortedTraces.length})
            </span>
            {selectedIds.length > 0 && (
              <button
                onClick={clearSelection}
                className="text-[12px] text-envoi-text-dim transition-colors hover:text-envoi-text"
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
                className="h-auto max-w-35 gap-1 rounded-[3px] border-none bg-transparent px-1.5 py-0.75 text-[13px] font-medium text-envoi-text-dim shadow-none hover:bg-envoi-border-light hover:text-envoi-text [&>span]:truncate"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent
                position="popper"
                className="border-envoi-border bg-envoi-bg font-mono shadow-md"
              >
                <SelectItem
                  value="all"
                  className="text-[12px] text-envoi-text focus:bg-envoi-surface"
                >
                  All models
                </SelectItem>
                {uniqueModels.map((model) => (
                  <SelectItem
                    key={model}
                    value={model}
                    className="text-[12px] text-envoi-text focus:bg-envoi-surface"
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
                <button className="flex shrink-0 items-center gap-0.75 rounded-[3px] px-1.5 py-0.75 text-[13px] font-medium text-envoi-text-dim transition-colors hover:bg-envoi-border-light hover:text-envoi-text">
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
                  className="text-[12px] text-envoi-text focus:bg-envoi-surface"
                  style={{
                    fontWeight: sortBy === "score" ? 600 : 400,
                    color: sortBy === "score" ? T.accent : T.text,
                  }}
                >
                  Score
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => setSortBy("date")}
                  className="text-[12px] text-envoi-text focus:bg-envoi-surface"
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

          {/* Trace list grouped by environment */}
          <div className="flex-1 overflow-y-auto">
            {[...sidebarGroups.entries()].map(([environment, traces]) => (
              <div key={environment}>
                {/* Environment section header */}
                <div className="border-b border-envoi-border bg-envoi-bg px-3.5 py-1.5">
                  <span className="text-[12px] font-bold uppercase tracking-[0.08em] text-envoi-text-dim">
                    {environment}
                  </span>
                </div>

                {traces.map((trace) => {
                  const currentFlatIndex = flatIndex;
                  flatIndex++;
                  return (
                    <TraceRow
                      key={trace.id}
                      trace={trace}
                      flatIndex={currentFlatIndex}
                      focusedIndex={focusedIndex}
                      focusedRowRef={focusedRowRef}
                      getColorIndex={getColorIndex}
                      toggleTrace={toggleTrace}
                      setFocusedIndex={setFocusedIndex}
                      computeTraceTotal={computeTraceTotal}
                      showYear={showYear}
                    />
                  );
                })}
              </div>
            ))}
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
            children
          )}
        </div>
      </div>
    </div>
  );
}

/** Individual trace row in the sidebar */
function TraceRow({
  trace,
  flatIndex,
  focusedIndex,
  focusedRowRef,
  getColorIndex,
  toggleTrace,
  setFocusedIndex,
  computeTraceTotal,
  showYear,
}: {
  trace: Trajectory;
  flatIndex: number;
  focusedIndex: number;
  focusedRowRef: React.RefObject<HTMLDivElement | null>;
  getColorIndex: (id: string) => number;
  toggleTrace: (id: string) => void;
  setFocusedIndex: (index: number) => void;
  computeTraceTotal: (trace: Trajectory) => number;
  showYear: boolean;
}) {
  const colorIdx = getColorIndex(trace.id);
  const isSelected = colorIdx >= 0;
  const isFocused = flatIndex === focusedIndex;
  const color = isSelected
    ? TRACE_COLORS[colorIdx % TRACE_COLORS.length]
    : undefined;
  const traceTotal = computeTraceTotal(trace);

  return (
    <div
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
        setFocusedIndex(flatIndex);
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
        <span className="truncate text-[13px] font-medium text-envoi-text">
          {trace.id}
        </span>
        <span className="truncate text-[13px] text-envoi-text-dim">
          {trace.model}
        </span>
        <span className="text-[13px] text-envoi-text-dim">
          {formatDate(trace.startedAt, showYear)}
        </span>
        {/* Score bar */}
        <div className="mt-px h-0.75 w-full rounded-full bg-envoi-border-light">
          <div
            className="h-full rounded-full"
            style={{
              width: traceTotal > 0 ? `${(trace.finalPassed / traceTotal) * 100}%` : "0%",
              background: isSelected && color ? color.line : T.textDim,
            }}
          />
        </div>
      </div>

      {/* Score */}
      <div className="flex shrink-0 flex-col items-end gap-0.5">
        <span className="text-[13px] font-semibold text-envoi-text">
          {trace.finalPassed}
        </span>
        <span className="text-[13px] text-envoi-text-dim">
          {formatPercent(trace.finalPassed, traceTotal)}
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
}
