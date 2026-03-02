/**
 * Setup Compare — group trajectories by configurable dimensions and compare medians.
 * Client component: manages grouping dimensions, visibility toggles, and interactions.
 *
 * Charts use percentage Y-axis (0–100%) so groups spanning different environments
 * are normalized. Per-suite median table is grouped by environment to prevent
 * mixing cross-environment suite data.
 *
 * Sidebar: Dimension chips (add/remove) + group list with eye toggles.
 * Main: Median progress curves per group, per-suite median table, group->trace breakdown.
 */

"use client";

import { useState, useMemo } from "react";
import { Eye, EyeOff, X, Plus, ChevronDown, ChevronRight } from "lucide-react";
import type { Trajectory, TrajectoryGroup, Commit, Suite } from "@/lib/types";
import { GROUP_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { GROUPABLE_DIMENSIONS } from "@/lib/constants";
import { groupTraces, median, formatPercent, formatDuration, computeMaxDuration, getXTicks, formatXTick } from "@/lib/utils";

type SetupCompareProps = {
  allTraces: Trajectory[];
};

/** Chart layout constants for the median curves chart */
const VIEW_WIDTH = 900;
const VIEW_HEIGHT = 340;
const MARGIN = { top: 20, right: 20, bottom: 40, left: 55 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Fixed percentage ticks for Y axis */
const Y_PCT_TICKS = [0, 25, 50, 75, 100];

function toX(minutes: number, maxDuration: number): number {
  if (maxDuration === 0) {
    return MARGIN.left;
  }
  return MARGIN.left + (minutes / maxDuration) * PLOT_WIDTH;
}

/** Map percentage (0–100) to Y pixel position */
function toYPct(pct: number): number {
  return MARGIN.top + PLOT_HEIGHT - (pct / 100) * PLOT_HEIGHT;
}

/** Compute a median progress curve from a set of traces (returns percentages) */
type MedianPoint = {
  minutes: number;
  medianPct: number;
};

function computeMedianCurve(traces: Trajectory[], maxDuration: number): MedianPoint[] {
  if (traces.length === 0) {
    return [];
  }

  /** Sample at regular time intervals */
  const numSamples = 48;
  const stepMinutes = maxDuration / numSamples;

  return Array.from({ length: numSamples + 1 }, (_, sampleIdx) => {
    const targetMinutes = sampleIdx * stepMinutes;
    const pctValues = traces.map((trace) => {
      const eligible = trace.commits.filter(
        (commit) => commit.minutesElapsed <= targetMinutes,
      );
      const lastEligible = eligible[eligible.length - 1];
      const passed = lastEligible?.totalPassed ?? 0;
      return trace.totalTests > 0 ? (passed / trace.totalTests) * 100 : 0;
    });

    return {
      minutes: targetMinutes,
      medianPct: median(pctValues),
    };
  });
}

/** Build SVG line path from median points */
function buildMedianLinePath(points: MedianPoint[], maxDuration: number): string {
  return points
    .map((point, pointIdx) => {
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${toX(point.minutes, maxDuration).toFixed(1)},${toYPct(point.medianPct).toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG area path from median points */
function buildMedianAreaPath(points: MedianPoint[], maxDuration: number): string {
  if (points.length === 0) {
    return "";
  }
  const firstPoint = points[0];
  const lastPoint = points[points.length - 1];
  if (!firstPoint || !lastPoint) {
    return "";
  }
  const lineSegments = points
    .map((point, pointIdx) => {
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${toX(point.minutes, maxDuration).toFixed(1)},${toYPct(point.medianPct).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${toX(lastPoint.minutes, maxDuration).toFixed(1)},${toYPct(0).toFixed(1)}`;
  const bottomLeft = `L${toX(firstPoint.minutes, maxDuration).toFixed(1)},${toYPct(0).toFixed(1)}`;
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

/** Derive which suites belong to which environment from trace data */
function deriveEnvSuites(traces: Trajectory[]): Map<string, Suite[]> {
  const envSuiteMap = new Map<string, Map<string, number>>();
  for (const trace of traces) {
    const env = trace.environment || "unknown";
    let suiteMap = envSuiteMap.get(env);
    if (!suiteMap) {
      suiteMap = new Map();
      envSuiteMap.set(env, suiteMap);
    }
    if (trace.suites) {
      for (const suite of trace.suites) {
        const existing = suiteMap.get(suite.name);
        if (existing === undefined || suite.total > existing) {
          suiteMap.set(suite.name, suite.total);
        }
      }
    }
  }
  return new Map(
    [...envSuiteMap.entries()]
      .sort(([envA], [envB]) => envA.localeCompare(envB))
      .map(([env, suiteMap]) => [
        env,
        [...suiteMap.entries()]
          .map(([name, total]) => ({ name, total }))
          .sort((suiteA, suiteB) => suiteA.name.localeCompare(suiteB.name)),
      ] as [string, Suite[]]),
  );
}

export function SetupCompare({ allTraces }: SetupCompareProps) {
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

  const envSuites = useMemo(() => deriveEnvSuites(allTraces), [allTraces]);

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

  const maxDuration = useMemo(() => computeMaxDuration(allTraces), [allTraces]);
  const xTicks = useMemo(() => getXTicks(maxDuration), [maxDuration]);

  return (
    <div className="flex flex-1 gap-0 overflow-hidden">
      {/* Sidebar */}
      <div className="flex w-65 shrink-0 flex-col border-r border-envoi-border">
        {/* Dimension chips header */}
        <div className="border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5">
          <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Group By
          </span>
        </div>

        {/* Active dimension chips */}
        <div className="flex flex-wrap gap-1.5 border-b border-envoi-border px-3.5 py-2.5">
          {activeDimensions.map((dimKey) => {
            const dimDef = GROUPABLE_DIMENSIONS.find((dim) => dim.key === dimKey);
            return (
              <button
                key={dimKey}
                onClick={() => removeDimension(dimKey)}
                className="flex items-center gap-1 rounded-[3px] bg-envoi-accent-bg px-2 py-0.75 text-[12px] font-medium whitespace-nowrap text-envoi-accent-dark transition-colors hover:bg-envoi-accent/10"
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
        <div className="border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5">
          <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Groups ({groups.length})
          </span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {groups.map((group, groupIndex) => {
            const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length];
            if (!color) {
              return undefined;
            }
            const isHidden = hiddenGroups.has(group.key);
            const medianPct = median(
              group.traces.map((trace) =>
                trace.totalTests > 0 ? (trace.finalPassed / trace.totalTests) * 100 : 0,
              ),
            );

            return (
              <div
                key={group.key}
                className="border-b border-envoi-border-light"
              >
                <div
                  className="flex items-center gap-2 px-3.5 py-2.5 transition-colors hover:bg-envoi-surface"
                  style={{ opacity: isHidden ? 0.4 : 1 }}
                >
                  {/* Color dot */}
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: color.line }}
                  />

                  {/* Label + stats */}
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate text-[13px] font-medium text-envoi-text">
                      {group.label}
                    </span>
                    <span className="text-[13px] text-envoi-text-dim">
                      {group.traces.length} traces &middot; med {medianPct.toFixed(1)}%
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
        {/* Median progress curves (percentage Y axis) */}
        <div className="mb-4 rounded border border-envoi-border bg-envoi-bg p-3">
          <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Median Progress Curves
          </div>
          <svg
            viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
            className="w-full"
            style={{ fontFamily: T.mono }}
          >
            {/* Grid */}
            {Y_PCT_TICKS.map((pct) => (
              <line
                key={`y-grid-${pct}`}
                x1={MARGIN.left}
                y1={toYPct(pct)}
                x2={VIEW_WIDTH - MARGIN.right}
                y2={toYPct(pct)}
                stroke={T.borderLight}
                strokeWidth={1}
              />
            ))}
            {xTicks.map((tick) => (
              <line
                key={`x-grid-${tick}`}
                x1={toX(tick, maxDuration)}
                y1={MARGIN.top}
                x2={toX(tick, maxDuration)}
                y2={MARGIN.top + PLOT_HEIGHT}
                stroke={T.borderLight}
                strokeWidth={1}
              />
            ))}

            {/* Y labels (left: percentage) */}
            {Y_PCT_TICKS.map((pct) => (
              <text
                key={`y-label-${pct}`}
                x={MARGIN.left - 8}
                y={toYPct(pct) + 3}
                textAnchor="end"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {pct}%
              </text>
            ))}

            {/* X labels */}
            {xTicks.map((tick) => (
              <text
                key={`x-label-${tick}`}
                x={toX(tick, maxDuration)}
                y={MARGIN.top + PLOT_HEIGHT + 20}
                textAnchor="middle"
                style={{ fontSize: "9px", fill: T.textDim }}
              >
                {formatXTick(tick)}
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
              MEDIAN TESTS PASSED (%)
            </text>

            {/* Group curves */}
            {visibleGroups.map((group, groupIndex) => {
              const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length];
              if (!color) {
                return undefined;
              }
              const curve = computeMedianCurve(group.traces, maxDuration);
              const lastPoint = curve[curve.length - 1];

              return (
                <g key={group.key}>
                  <path
                    d={buildMedianAreaPath(curve, maxDuration)}
                    fill={color.fill}
                  />
                  <path
                    d={buildMedianLinePath(curve, maxDuration)}
                    fill="none"
                    stroke={color.line}
                    strokeWidth={1.5}
                  />
                  {lastPoint && (
                    <text
                      x={toX(lastPoint.minutes, maxDuration) + 6}
                      y={toYPct(lastPoint.medianPct) + 3}
                      style={{ fontSize: "10px", fill: color.line, fontWeight: 700 }}
                    >
                      {lastPoint.medianPct.toFixed(1)}%
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        {/* Per-suite median table (grouped by environment) */}
        <div className="mb-4 rounded border border-envoi-border bg-envoi-bg">
          <div className="border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5">
            <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Per-Suite Medians
            </span>
          </div>

          {/* Table header */}
          <div className="flex items-center border-b border-envoi-border px-3.5 py-2">
            <span className="min-w-25 text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Suite
            </span>
            <span className="min-w-12.5 text-right text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Total
            </span>
            {visibleGroups.map((group, groupIndex) => {
              const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length];
              if (!color) {
                return undefined;
              }
              return (
                <span
                  key={group.key}
                  className="flex min-w-40 flex-1 items-center gap-1 border-l border-envoi-border-light pl-4 text-[12px] font-semibold uppercase tracking-[0.08em]"
                  style={{ color: color.line }}
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: color.line }}
                  />
                  {group.label.length > 16 ? group.label.slice(0, 16) + "..." : group.label}
                </span>
              );
            })}
          </div>

          {/* Suite rows grouped by environment */}
          {[...envSuites.entries()].map(([environment, suites]) => (
            <div key={environment}>
              {/* Environment section header — only if multiple environments */}
              {envSuites.size > 1 && (
                <div className="border-b border-envoi-border bg-envoi-bg px-3.5 py-1.5">
                  <span className="text-[12px] font-bold uppercase tracking-[0.08em] text-envoi-text-dim">
                    {environment}
                  </span>
                </div>
              )}

              {suites.map((suite) => {
                const suiteColor = SUITE_COLORS[suite.name];
                return (
                  <div
                    key={suite.name}
                    className="flex items-center border-b border-envoi-border-light px-3.5 py-2 transition-colors hover:bg-envoi-surface"
                  >
                    <span className="flex min-w-25 items-center gap-2 text-[13px] font-medium text-envoi-text">
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ background: suiteColor?.color ?? T.textMuted }}
                      />
                      {suite.name}
                    </span>
                    <span className="min-w-12.5 text-right text-[13px] text-envoi-text-muted">
                      {suite.total}
                    </span>
                    {visibleGroups.map((group, groupIndex) => {
                      const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length];
                      if (!color) {
                        return undefined;
                      }
                      /** Only compute median from traces in this environment */
                      const envTraces = group.traces.filter(
                        (trace) => (trace.environment || "unknown") === environment,
                      );
                      if (envTraces.length === 0) {
                        return (
                          <div
                            key={`${group.key}-${suite.name}`}
                            className="flex min-w-40 flex-1 items-center gap-2 border-l border-envoi-border-light pl-4"
                          >
                            <span className="text-[12px] text-envoi-text-dim">&mdash;</span>
                          </div>
                        );
                      }
                      const medianVal = suiteMedianFinal(envTraces, suite.name);
                      const pct = (medianVal / suite.total) * 100;
                      return (
                        <div
                          key={`${group.key}-${suite.name}`}
                          className="flex min-w-40 flex-1 items-center gap-2 border-l border-envoi-border-light pl-4"
                        >
                          <div className="h-1 w-20 rounded-full bg-envoi-border-light">
                            <div
                              className="h-full rounded-full"
                              style={{ width: `${pct}%`, background: color.line }}
                            />
                          </div>
                          <span className="text-[13px] font-semibold" style={{ color: color.line }}>
                            {Math.round(medianVal)}
                          </span>
                          <span className="text-[13px] text-envoi-text-dim">
                            {formatPercent(medianVal, suite.total)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {/* Group -> Trace breakdown table */}
        <div className="rounded border border-envoi-border bg-envoi-bg">
          <div className="border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5">
            <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
              Group Breakdown
            </span>
          </div>

          {groups.map((group: TrajectoryGroup, groupIndex: number) => {
            const color = GROUP_COLORS[groupIndex % GROUP_COLORS.length];
            if (!color) {
              return undefined;
            }
            const isExpanded = expandedGroups.has(group.key);
            const medianPct = median(
              group.traces.map((trace) =>
                trace.totalTests > 0 ? (trace.finalPassed / trace.totalTests) * 100 : 0,
              ),
            );
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
                  className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-envoi-surface"
                >
                  {isExpanded ? (
                    <ChevronDown size={12} className="shrink-0 text-envoi-text-dim" />
                  ) : (
                    <ChevronRight size={12} className="shrink-0 text-envoi-text-dim" />
                  )}
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: color.line }}
                  />
                  <span className="flex-1 truncate text-[13px] font-medium text-envoi-text">
                    {group.label}
                  </span>
                  <span className="text-[12px] text-envoi-text-dim">
                    {group.traces.length} traces
                  </span>
                  <span className="text-[12px] text-envoi-text-muted">
                    med {medianPct.toFixed(1)}% &middot; {formatDuration(Math.round(medianDuration))}
                  </span>
                </button>

                {/* Expanded trace list */}
                {isExpanded && (
                  <div className="border-t border-envoi-border-light bg-envoi-surface/50">
                    {group.traces.map((trace) => {
                      const lastTraceCommit = trace.commits[trace.commits.length - 1];
                      const tracePct = trace.totalTests > 0
                        ? ((lastTraceCommit?.totalPassed ?? 0) / trace.totalTests) * 100
                        : 0;
                      return (
                        <div
                          key={trace.id}
                          className="flex items-center gap-3 border-b border-envoi-border-light px-3.5 py-2 pl-9.5"
                        >
                          <span className="min-w-20 truncate text-[12px] font-medium text-envoi-text">
                            {trace.id}
                          </span>
                          <span className="min-w-25 truncate text-[12px] text-envoi-text-muted">
                            {trace.model}
                          </span>
                          <span className="text-[12px] text-envoi-text-muted">
                            {trace.duration}
                          </span>
                          <div className="flex flex-1 items-center gap-2">
                            <div className="h-1 w-22.5 rounded-full bg-envoi-border-light">
                              <div
                                className="h-full rounded-full"
                                style={{
                                  width: `${tracePct}%`,
                                  background: color.line,
                                }}
                              />
                            </div>
                            <span className="text-[12px] font-semibold text-envoi-text">
                              {lastTraceCommit?.totalPassed ?? 0}
                            </span>
                            <span className="text-[13px] text-envoi-text-dim">
                              / {trace.totalTests}
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
        className="flex items-center gap-0.75 rounded-[3px] border border-envoi-border px-1.5 py-0.75 text-[12px] text-envoi-text-dim transition-colors hover:bg-envoi-surface hover:text-envoi-text"
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
              className="flex w-full items-center px-3 py-1.5 text-[12px] whitespace-nowrap text-envoi-text transition-colors hover:bg-envoi-surface"
            >
              {dim.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
