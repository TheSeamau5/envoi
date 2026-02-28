/**
 * Setup Compare — group trajectories by configurable dimensions and compare medians.
 * Client component: manages grouping dimensions, visibility toggles, and interactions.
 *
 * Sidebar: Dimension chips (add/remove) + group list with eye toggles.
 * Main: Median progress curves per group, per-suite median table, group->trace breakdown.
 */

"use client";

import { useState, useMemo } from "react";
import { Eye, EyeOff, X, Plus, ChevronDown, ChevronRight } from "lucide-react";
import type { Trajectory, TrajectoryGroup, Commit, Suite } from "@/lib/types";
import { GROUP_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { GROUPABLE_DIMENSIONS, MAX_DURATION, TOTAL_TESTS as DEFAULT_TOTAL_TESTS, SUITES as DEFAULT_SUITES } from "@/lib/constants";
import { groupTraces, median, formatPercent, formatDuration } from "@/lib/utils";

type SetupCompareProps = {
  allTraces: Trajectory[];
  suites?: Suite[];
  totalTests?: number;
};

/** Chart layout constants for the median curves chart */
const VIEW_WIDTH = 900;
const VIEW_HEIGHT = 340;
const MARGIN = { top: 20, right: 60, bottom: 40, left: 55 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

function toX(minutes: number): number {
  return MARGIN.left + (minutes / MAX_DURATION) * PLOT_WIDTH;
}

function toY(passed: number, totalTests: number): number {
  return MARGIN.top + PLOT_HEIGHT - (passed / totalTests) * PLOT_HEIGHT;
}

/** Compute a median progress curve from a set of traces */
type MedianPoint = {
  minutes: number;
  medianPassed: number;
};

function computeMedianCurve(traces: Trajectory[]): MedianPoint[] {
  if (traces.length === 0) return [];

  /** Sample at regular time intervals */
  const numSamples = 48;
  const stepMinutes = MAX_DURATION / numSamples;

  return Array.from({ length: numSamples + 1 }, (_, sampleIdx) => {
    const targetMinutes = sampleIdx * stepMinutes;
    const passedValues = traces.map((trace) => {
      const eligible = trace.commits.filter(
        (commit) => commit.minutesElapsed <= targetMinutes,
      );
      const lastEligible = eligible[eligible.length - 1];
      return lastEligible?.totalPassed ?? 0;
    });

    return {
      minutes: targetMinutes,
      medianPassed: median(passedValues),
    };
  });
}

/** Build SVG line path from median points */
function buildMedianLinePath(points: MedianPoint[], totalTests: number): string {
  return points
    .map((point, pointIdx) => {
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${toX(point.minutes).toFixed(1)},${toY(point.medianPassed, totalTests).toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG area path from median points */
function buildMedianAreaPath(points: MedianPoint[], totalTests: number): string {
  if (points.length === 0) return "";
  const firstPoint = points[0]!;
  const lastPoint = points[points.length - 1]!;
  const lineSegments = points
    .map((point, pointIdx) => {
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${toX(point.minutes).toFixed(1)},${toY(point.medianPassed, totalTests).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${toX(lastPoint.minutes).toFixed(1)},${toY(0, totalTests).toFixed(1)}`;
  const bottomLeft = `L${toX(firstPoint.minutes).toFixed(1)},${toY(0, totalTests).toFixed(1)}`;
  return `${lineSegments} ${bottomRight} ${bottomLeft} Z`;
}

/** Compute median final passed for a suite across traces */
function suiteMedianFinal(traces: Trajectory[], suiteName: string): number {
  const values = traces.map((trace) => {
    const lastCommit = trace.commits[trace.commits.length - 1];
    return lastCommit?.suiteState[suiteName] ?? 0;
  });
  return median(values);
}

/** X-axis tick generator */
function getXTicks(): number[] {
  return Array.from({ length: 9 }, (_, hourIdx) => hourIdx * 60);
}

/** Y-axis tick generator */
function getYTicks(totalTests: number): number[] {
  return Array.from({ length: 5 }, (_, tickIdx) => Math.round((tickIdx / 4) * totalTests));
}

export function SetupCompare({ allTraces, suites: suitesProp, totalTests: totalTestsProp }: SetupCompareProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const effectiveTotal = totalTestsProp ?? DEFAULT_TOTAL_TESTS;
  const [activeDimensions, setActiveDimensions] = useState<string[]>(["model"]);
  const [hiddenGroups, setHiddenGroups] = useState<Set<string>>(new Set());
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const groups = useMemo(
    () => groupTraces(allTraces, activeDimensions),
    [allTraces, activeDimensions],
  );

  const visibleGroups = useMemo(
    () => groups.filter((group) => !hiddenGroups.has(group.key)),
    [groups, hiddenGroups],
  );

  const availableDimensions = GROUPABLE_DIMENSIONS.filter(
    (dim) => !activeDimensions.includes(dim.key),
  );

  function addDimension(key: string) {
    setActiveDimensions((prev) => [...prev, key]);
    setHiddenGroups(new Set());
  }

  function removeDimension(key: string) {
    setActiveDimensions((prev) => prev.filter((dim) => dim !== key));
    setHiddenGroups(new Set());
  }

  function toggleGroupVisibility(groupKey: string) {
    setHiddenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) {
        next.delete(groupKey);
      } else {
        next.add(groupKey);
      }
      return next;
    });
  }

  function toggleGroupExpand(groupKey: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) {
        next.delete(groupKey);
      } else {
        next.add(groupKey);
      }
      return next;
    });
  }

  const xTicks = getXTicks();
  const yTicks = getYTicks(effectiveTotal);

  return (
    <div className="flex flex-1 gap-0 overflow-hidden">
      {/* Sidebar */}
      <div className="flex w-[260px] shrink-0 flex-col border-r border-envoi-border">
        {/* Dimension chips header */}
        <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
          <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Group By
          </span>
        </div>

        {/* Active dimension chips */}
        <div className="flex flex-wrap gap-[6px] border-b border-envoi-border px-[14px] py-[10px]">
          {activeDimensions.map((dimKey) => {
            const dimDef = GROUPABLE_DIMENSIONS.find((dim) => dim.key === dimKey);
            return (
              <button
                key={dimKey}
                onClick={() => removeDimension(dimKey)}
                className="flex items-center gap-[4px] rounded-[3px] bg-envoi-accent-bg px-[8px] py-[3px] text-[10px] font-medium whitespace-nowrap text-envoi-accent-dark transition-colors hover:bg-envoi-accent/10"
              >
                {dimDef?.label ?? dimKey}
                <X size={9} />
              </button>
            );
          })}

          {/* Add dimension dropdown */}
          {availableDimensions.length > 0 && (
            <div className="relative">
              <AddDimensionButton
                available={availableDimensions}
                onAdd={addDimension}
              />
            </div>
          )}
        </div>

        {/* Group list */}
        <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
          <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Groups ({groups.length})
          </span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {groups.map((group, groupIndex) => {
            const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length]!;
            const isHidden = hiddenGroups.has(group.key);
            const medianFinal = median(group.traces.map((trace) => trace.finalPassed));

            return (
              <div
                key={group.key}
                className="border-b border-envoi-border-light"
              >
                <div
                  className="flex items-center gap-[8px] px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
                  style={{ opacity: isHidden ? 0.4 : 1 }}
                >
                  {/* Color dot */}
                  <span
                    className="h-[8px] w-[8px] shrink-0 rounded-full"
                    style={{ background: color.line }}
                  />

                  {/* Label + stats */}
                  <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
                    <span className="truncate text-[11px] font-medium text-envoi-text">
                      {group.label}
                    </span>
                    <span className="text-[9px] text-envoi-text-dim">
                      {group.traces.length} traces &middot; med {Math.round(medianFinal)} passed
                    </span>
                  </div>

                  {/* Eye toggle */}
                  <button
                    onClick={() => toggleGroupVisibility(group.key)}
                    className="shrink-0 text-envoi-text-dim transition-colors hover:text-envoi-text"
                  >
                    {isHidden ? <EyeOff size={12} /> : <Eye size={12} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-y-auto p-4">
        {/* Median progress curves */}
        <div className="mb-4 rounded border border-envoi-border bg-envoi-bg p-3">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Median Progress Curves
          </div>
          <svg
            viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
            className="w-full"
            style={{ fontFamily: T.mono }}
          >
            {/* Grid */}
            {yTicks.map((tick) => (
              <line
                key={`y-grid-${tick}`}
                x1={MARGIN.left}
                y1={toY(tick, effectiveTotal)}
                x2={VIEW_WIDTH - MARGIN.right}
                y2={toY(tick, effectiveTotal)}
                stroke={T.borderLight}
                strokeWidth={1}
              />
            ))}
            {xTicks.map((tick) => (
              <line
                key={`x-grid-${tick}`}
                x1={toX(tick)}
                y1={MARGIN.top}
                x2={toX(tick)}
                y2={MARGIN.top + PLOT_HEIGHT}
                stroke={T.borderLight}
                strokeWidth={1}
              />
            ))}

            {/* Y labels (left: absolute count) */}
            {yTicks.map((tick) => (
              <text
                key={`y-label-${tick}`}
                x={MARGIN.left - 8}
                y={toY(tick, effectiveTotal) + 3}
                textAnchor="end"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {tick}
              </text>
            ))}

            {/* Y labels (right: percentage) */}
            {yTicks.map((tick) => (
              <text
                key={`y-pct-${tick}`}
                x={VIEW_WIDTH - MARGIN.right + 8}
                y={toY(tick, effectiveTotal) + 3}
                textAnchor="start"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {`${Math.round((tick / effectiveTotal) * 100)}%`}
              </text>
            ))}

            {/* X labels */}
            {xTicks.map((tick) => (
              <text
                key={`x-label-${tick}`}
                x={toX(tick)}
                y={MARGIN.top + PLOT_HEIGHT + 20}
                textAnchor="middle"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {`${tick / 60}h`}
              </text>
            ))}

            {/* Axis titles */}
            <text
              x={VIEW_WIDTH / 2}
              y={VIEW_HEIGHT - 4}
              textAnchor="middle"
              style={{ fontSize: "9px", fill: T.textMuted, fontWeight: 600 }}
            >
              ELAPSED TIME
            </text>
            <text
              x={12}
              y={MARGIN.top + PLOT_HEIGHT / 2}
              textAnchor="middle"
              transform={`rotate(-90, 12, ${MARGIN.top + PLOT_HEIGHT / 2})`}
              style={{ fontSize: "9px", fill: T.textMuted, fontWeight: 600 }}
            >
              MEDIAN TESTS PASSED
            </text>

            {/* Group curves */}
            {visibleGroups.map((group, groupIndex) => {
              const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length]!;
              const curve = computeMedianCurve(group.traces);
              const lastPoint = curve[curve.length - 1];

              return (
                <g key={group.key}>
                  <path
                    d={buildMedianAreaPath(curve, effectiveTotal)}
                    fill={color.fill}
                  />
                  <path
                    d={buildMedianLinePath(curve, effectiveTotal)}
                    fill="none"
                    stroke={color.line}
                    strokeWidth={1.5}
                  />
                  {lastPoint && (
                    <text
                      x={toX(lastPoint.minutes) + 6}
                      y={toY(lastPoint.medianPassed, effectiveTotal) + 3}
                      style={{ fontSize: "8px", fill: color.line, fontWeight: 700 }}
                    >
                      {Math.round(lastPoint.medianPassed)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        {/* Per-suite median table */}
        <div className="mb-4 rounded border border-envoi-border bg-envoi-bg">
          <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Per-Suite Medians
            </span>
          </div>

          {/* Table header */}
          <div className="flex items-center border-b border-envoi-border px-[14px] py-[8px]">
            <span className="min-w-[100px] text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Suite
            </span>
            <span className="min-w-[50px] text-right text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Total
            </span>
            {visibleGroups.map((group, groupIndex) => {
              const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length]!;
              return (
                <span
                  key={group.key}
                  className="flex min-w-[160px] flex-1 items-center gap-[4px] border-l border-envoi-border-light pl-4 text-[10px] font-semibold uppercase tracking-[0.08em]"
                  style={{ color: color.line }}
                >
                  <span
                    className="h-[6px] w-[6px] rounded-full"
                    style={{ background: color.line }}
                  />
                  {group.label.length > 16 ? group.label.slice(0, 16) + "..." : group.label}
                </span>
              );
            })}
          </div>

          {/* Suite rows */}
          {effectiveSuites.map((suite) => {
            const suiteColor = SUITE_COLORS[suite.name];
            return (
              <div
                key={suite.name}
                className="flex items-center border-b border-envoi-border-light px-[14px] py-[8px] transition-colors hover:bg-envoi-surface"
              >
                <span className="flex min-w-[100px] items-center gap-2 text-[11px] font-medium text-envoi-text">
                  <span
                    className="h-[6px] w-[6px] rounded-full"
                    style={{ background: suiteColor?.color ?? T.textMuted }}
                  />
                  {suite.name}
                </span>
                <span className="min-w-[50px] text-right text-[11px] text-envoi-text-muted">
                  {suite.total}
                </span>
                {visibleGroups.map((group, groupIndex) => {
                  const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length]!;
                  const medianVal = suiteMedianFinal(group.traces, suite.name);
                  const pct = (medianVal / suite.total) * 100;
                  return (
                    <div
                      key={`${group.key}-${suite.name}`}
                      className="flex min-w-[160px] flex-1 items-center gap-2 border-l border-envoi-border-light pl-4"
                    >
                      <div className="h-[4px] w-[80px] rounded-full bg-envoi-border-light">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, background: color.line }}
                        />
                      </div>
                      <span className="text-[11px] font-semibold" style={{ color: color.line }}>
                        {Math.round(medianVal)}
                      </span>
                      <span className="text-[9px] text-envoi-text-dim">
                        {formatPercent(medianVal, suite.total)}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* Group -> Trace breakdown table */}
        <div className="rounded border border-envoi-border bg-envoi-bg">
          <div className="border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
            <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Group Breakdown
            </span>
          </div>

          {groups.map((group: TrajectoryGroup, groupIndex: number) => {
            const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length]!;
            const isExpanded = expandedGroups.has(group.key);
            const medianFinal = median(group.traces.map((trace) => trace.finalPassed));
            const lastCommits: Commit[] = group.traces
              .map((trace) => trace.commits[trace.commits.length - 1])
              .filter((commit): commit is Commit => commit !== undefined);
            const medianDuration = median(
              lastCommits.map((commit) => commit.minutesElapsed),
            );

            return (
              <div key={group.key} className="border-b border-envoi-border-light">
                {/* Group header row */}
                <button
                  onClick={() => toggleGroupExpand(group.key)}
                  className="flex w-full items-center gap-[8px] px-[14px] py-[10px] text-left transition-colors hover:bg-envoi-surface"
                >
                  {isExpanded ? (
                    <ChevronDown size={12} className="shrink-0 text-envoi-text-dim" />
                  ) : (
                    <ChevronRight size={12} className="shrink-0 text-envoi-text-dim" />
                  )}
                  <span
                    className="h-[8px] w-[8px] shrink-0 rounded-full"
                    style={{ background: color.line }}
                  />
                  <span className="flex-1 truncate text-[11px] font-medium text-envoi-text">
                    {group.label}
                  </span>
                  <span className="text-[10px] text-envoi-text-dim">
                    {group.traces.length} traces
                  </span>
                  <span className="text-[10px] text-envoi-text-muted">
                    med {Math.round(medianFinal)} &middot; {formatDuration(Math.round(medianDuration))}
                  </span>
                </button>

                {/* Expanded trace list */}
                {isExpanded && (
                  <div className="border-t border-envoi-border-light bg-envoi-surface/50">
                    {group.traces.map((trace) => {
                      const lastTraceCommit = trace.commits[trace.commits.length - 1];
                      return (
                        <div
                          key={trace.id}
                          className="flex items-center gap-3 border-b border-envoi-border-light px-[14px] py-[8px] pl-[38px]"
                        >
                          <span className="min-w-[80px] truncate text-[10px] font-medium text-envoi-text">
                            {trace.id}
                          </span>
                          <span className="min-w-[100px] truncate text-[10px] text-envoi-text-muted">
                            {trace.model}
                          </span>
                          <span className="text-[10px] text-envoi-text-muted">
                            {trace.duration}
                          </span>
                          <div className="flex flex-1 items-center gap-2">
                            <div className="h-[4px] w-[90px] rounded-full bg-envoi-border-light">
                              <div
                                className="h-full rounded-full"
                                style={{
                                  width: `${((lastTraceCommit?.totalPassed ?? 0) / effectiveTotal) * 100}%`,
                                  background: color.line,
                                }}
                              />
                            </div>
                            <span className="text-[10px] font-semibold text-envoi-text">
                              {lastTraceCommit?.totalPassed ?? 0}
                            </span>
                            <span className="text-[9px] text-envoi-text-dim">
                              / {effectiveTotal}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Simple dropdown button for adding a dimension */
function AddDimensionButton({
  available,
  onAdd,
}: {
  available: readonly { key: string; label: string }[];
  onAdd: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-[3px] rounded-[3px] border border-envoi-border px-[6px] py-[3px] text-[10px] text-envoi-text-dim transition-colors hover:bg-envoi-surface hover:text-envoi-text"
      >
        <Plus size={9} />
        Add
      </button>
      {open && (
        <div className="absolute left-0 top-full z-10 mt-1 rounded border border-envoi-border bg-envoi-bg shadow-sm">
          {available.map((dim) => (
            <button
              key={dim.key}
              onClick={() => {
                onAdd(dim.key);
                setOpen(false);
              }}
              className="flex w-full items-center px-3 py-[6px] text-[10px] whitespace-nowrap text-envoi-text transition-colors hover:bg-envoi-surface"
            >
              {dim.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
