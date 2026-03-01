/**
 * Progress Curves — overlaid SVG line chart showing test-pass progress over time.
 * Client component: renders interactive SVG with hover tooltips.
 *
 * X = elapsed time (auto-scaled to data), Y = tests passed (0–totalTests).
 * Each selected trace renders as a line with area fill, plus red dots at regressions.
 */

"use client";

import { useState, useMemo } from "react";
import { AlertCircle, Clock, Hash } from "lucide-react";
import type { Trajectory, Commit, Suite } from "@/lib/types";
import { TRACE_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { TOTAL_TESTS as DEFAULT_TOTAL_TESTS, SUITES as DEFAULT_SUITES } from "@/lib/constants";
import { formatDuration, formatPercent, computeMaxDuration, getXTicks, getYTicks, formatXTick } from "@/lib/utils";

type ProgressCurvesProps = {
  traces: Trajectory[];
  /** Stable color index for each trace (parallel to `traces` array) */
  colorIndices?: number[];
  suites?: Suite[];
  totalTests?: number;
};

/** Chart layout constants */
const VIEW_WIDTH = 900;
const VIEW_HEIGHT = 340;
const MARGIN = { top: 20, right: 60, bottom: 40, left: 55 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Map minutes elapsed to X pixel position */
function toX(minutes: number, maxDuration: number): number {
  if (maxDuration === 0) return MARGIN.left;
  return MARGIN.left + (minutes / maxDuration) * PLOT_WIDTH;
}

/** Map tests passed to Y pixel position */
function toY(passed: number, totalTests: number): number {
  if (totalTests === 0) return MARGIN.top + PLOT_HEIGHT;
  return MARGIN.top + PLOT_HEIGHT - (passed / totalTests) * PLOT_HEIGHT;
}

/** Build SVG path data for a trace line */
function buildLinePath(commits: Commit[], totalTests: number, maxDuration: number): string {
  return commits
    .map((commit, i) => {
      const cmd = i === 0 ? "M" : "L";
      return `${cmd}${toX(commit.minutesElapsed, maxDuration).toFixed(1)},${toY(commit.totalPassed, totalTests).toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG path data for the area fill under the curve */
function buildAreaPath(commits: Commit[], totalTests: number, maxDuration: number): string {
  if (commits.length === 0) return "";
  const firstCommit = commits[0];
  const lastCommit = commits[commits.length - 1];
  if (!firstCommit || !lastCommit) return "";
  const lineSegments = commits
    .map((commit, i) => {
      const cmd = i === 0 ? "M" : "L";
      return `${cmd}${toX(commit.minutesElapsed, maxDuration).toFixed(1)},${toY(commit.totalPassed, totalTests).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${toX(lastCommit.minutesElapsed, maxDuration).toFixed(1)},${toY(0, totalTests).toFixed(1)}`;
  const bottomLeft = `L${toX(firstCommit.minutesElapsed, maxDuration).toFixed(1)},${toY(0, totalTests).toFixed(1)}`;
  return `${lineSegments} ${bottomRight} ${bottomLeft} Z`;
}

export function ProgressCurves({ traces, colorIndices, suites: suitesProp, totalTests: totalTestsProp }: ProgressCurvesProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const effectiveTotal = totalTestsProp ?? DEFAULT_TOTAL_TESTS;

  const [hoveredTrace, setHoveredTrace] = useState<number | undefined>(undefined);

  const maxDuration = useMemo(() => computeMaxDuration(traces), [traces]);
  const xTicks = useMemo(() => getXTicks(maxDuration), [maxDuration]);
  const yTicks = getYTicks(effectiveTotal);

  return (
    <div className="flex flex-col gap-4">
      {/* SVG Chart */}
      <div className="rounded border border-envoi-border bg-envoi-bg p-3">
        <svg
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          className="w-full"
          style={{ fontFamily: T.mono }}
        >
          {/* Grid lines */}
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
              x1={toX(tick, maxDuration)}
              y1={MARGIN.top}
              x2={toX(tick, maxDuration)}
              y2={MARGIN.top + PLOT_HEIGHT}
              stroke={T.borderLight}
              strokeWidth={1}
            />
          ))}

          {/* Y axis labels (left: absolute count) */}
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

          {/* Y axis labels (right: percentage) */}
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

          {/* X axis labels */}
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
            TESTS PASSED
          </text>

          {/* Trace area fills (rendered first, behind lines) */}
          {traces.map((trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            return (
              <path
                key={`area-${trace.id}`}
                d={buildAreaPath(trace.commits, effectiveTotal, maxDuration)}
                fill={color.fill}
                opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
              />
            );
          })}

          {/* Trace lines */}
          {traces.map((trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            return (
              <path
                key={`line-${trace.id}`}
                d={buildLinePath(trace.commits, effectiveTotal, maxDuration)}
                fill="none"
                stroke={color.line}
                strokeWidth={hoveredTrace === traceIndex ? 2.5 : 1.5}
                opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
                onMouseEnter={() => setHoveredTrace(traceIndex)}
                onMouseLeave={() => setHoveredTrace(undefined)}
                style={{ cursor: "pointer" }}
              />
            );
          })}

          {/* Regression dots (red circles) */}
          {traces.map((trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            return trace.commits
              .filter((commit) => commit.isRegression)
              .map((commit) => (
                <circle
                  key={`reg-${trace.id}-${commit.index}`}
                  cx={toX(commit.minutesElapsed, maxDuration)}
                  cy={toY(commit.totalPassed, effectiveTotal)}
                  r={3}
                  fill={T.red}
                  stroke={T.bg}
                  strokeWidth={1}
                  opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.2 : 1}
                >
                  <title>
                    {`${color.label}: Regression at ${formatDuration(commit.minutesElapsed)} (${commit.delta} tests)`}
                  </title>
                </circle>
              ));
          })}

          {/* Endpoint score labels */}
          {traces.map((trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            const lastCommit = trace.commits[trace.commits.length - 1];
            if (!lastCommit) return undefined;
            return (
              <text
                key={`label-${trace.id}`}
                x={toX(lastCommit.minutesElapsed, maxDuration) + 6}
                y={toY(lastCommit.totalPassed, effectiveTotal) + 3}
                style={{
                  fontSize: "9px",
                  fill: color.line,
                  fontWeight: 700,
                }}
                opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
              >
                {`${color.label}: ${lastCommit.totalPassed}`}
              </text>
            );
          })}
        </svg>
      </div>

      {/* Stat cards per trace */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {traces.map((trace, traceIndex) => {
          const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
          if (!color) return undefined;
          const lastCommit = trace.commits[trace.commits.length - 1];
          const regressionCount = trace.commits.filter((commit) => commit.isRegression).length;
          const maxPassed = Math.max(...trace.commits.map((commit) => commit.totalPassed));

          return (
            <div
              key={trace.id}
              className="rounded border border-envoi-border bg-envoi-bg p-3"
              style={{ borderLeftColor: color.line, borderLeftWidth: 3 }}
            >
              <div className="mb-2 flex items-center gap-2">
                <span
                  className="flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold text-white"
                  style={{ background: color.line }}
                >
                  {color.label}
                </span>
                <span className="truncate text-[10px] text-envoi-text-muted">
                  {trace.model}
                </span>
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[10px] text-envoi-text-dim">
                    <Hash size={10} />
                    Final
                  </span>
                  <span className="text-[11px] font-semibold">
                    {lastCommit?.totalPassed ?? 0}
                    <span className="text-envoi-text-dim">
                      {" "}/ {effectiveTotal}
                    </span>
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[10px] text-envoi-text-dim">
                    <Hash size={10} />
                    Peak
                  </span>
                  <span className="text-[11px] font-semibold">
                    {maxPassed}
                    <span className="text-envoi-text-dim">
                      {" "}({formatPercent(maxPassed, effectiveTotal)})
                    </span>
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[10px] text-envoi-text-dim">
                    <Clock size={10} />
                    Duration
                  </span>
                  <span className="text-[11px] font-semibold">{trace.duration}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[10px] text-envoi-text-dim">
                    <AlertCircle size={10} />
                    Regressions
                  </span>
                  <span
                    className="text-[11px] font-semibold"
                    style={{ color: regressionCount > 0 ? T.red : T.text }}
                  >
                    {regressionCount}
                  </span>
                </div>
              </div>

              {/* Per-suite mini bars */}
              <div className="mt-2 space-y-1 border-t border-envoi-border-light pt-2">
                {effectiveSuites.map((suite) => {
                  const passed = lastCommit?.suiteState[suite.name] ?? 0;
                  const pct = (passed / suite.total) * 100;
                  const suiteColor = SUITE_COLORS[suite.name];
                  return (
                    <div key={suite.name} className="flex items-center gap-2">
                      <span className="w-[72px] truncate text-[9px] text-envoi-text-dim">
                        {suite.name}
                      </span>
                      <div className="h-[4px] flex-1 rounded-full bg-envoi-border-light">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${pct}%`,
                            background: suiteColor?.color ?? T.textMuted,
                          }}
                        />
                      </div>
                      <span className="w-[32px] text-right text-[9px] text-envoi-text-dim">
                        {passed}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
