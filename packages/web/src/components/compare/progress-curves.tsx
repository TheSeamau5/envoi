/**
 * Progress Curves — overlaid SVG line chart showing test-pass progress over time.
 * Client component: renders interactive SVG with hover tooltips.
 *
 * X = elapsed time (auto-scaled to data), Y = % tests passed (0–100%).
 * Each selected trace renders as a line with area fill, plus red dots at regressions.
 * Hover any data point to see a tooltip with % passed and dotted crosshair lines.
 */

"use client";

import { useState, useMemo, useCallback } from "react";
import { AlertCircle, Clock, Hash } from "lucide-react";
import type { Trajectory, Commit, Suite } from "@/lib/types";
import { TRACE_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { formatPercent, computeMaxDuration, getXTicks, formatXTick } from "@/lib/utils";

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
const MARGIN = { top: 20, right: 55, bottom: 40, left: 55 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Y-axis percentage ticks (always 0%, 25%, 50%, 75%, 100%) */
const Y_PCT_TICKS = [0, 25, 50, 75, 100];

/** Map minutes elapsed to X pixel position */
function toX(minutes: number, maxDuration: number): number {
  if (maxDuration === 0) return MARGIN.left;
  return MARGIN.left + (minutes / maxDuration) * PLOT_WIDTH;
}

/** Map percentage (0–100) to Y pixel position */
function toYPct(pct: number): number {
  return MARGIN.top + PLOT_HEIGHT - (pct / 100) * PLOT_HEIGHT;
}

/** Convert raw passed count to percentage */
function toPct(passed: number, total: number): number {
  if (total === 0) return 0;
  return (passed / total) * 100;
}

/** Build SVG path data for a trace line (using percentage Y) */
function buildLinePath(commits: Commit[], totalTests: number, maxDuration: number): string {
  return commits
    .map((commit, i) => {
      const cmd = i === 0 ? "M" : "L";
      return `${cmd}${toX(commit.minutesElapsed, maxDuration).toFixed(1)},${toYPct(toPct(commit.totalPassed, totalTests)).toFixed(1)}`;
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
      return `${cmd}${toX(commit.minutesElapsed, maxDuration).toFixed(1)},${toYPct(toPct(commit.totalPassed, totalTests)).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${toX(lastCommit.minutesElapsed, maxDuration).toFixed(1)},${toYPct(0).toFixed(1)}`;
  const bottomLeft = `L${toX(firstCommit.minutesElapsed, maxDuration).toFixed(1)},${toYPct(0).toFixed(1)}`;
  return `${lineSegments} ${bottomRight} ${bottomLeft} Z`;
}

type HoveredPoint = {
  traceIndex: number;
  commitIndex: number;
  commit: Commit;
  totalTests: number;
  cx: number;
  cy: number;
};

/** Check if all traces share the same suite names */
function tracesShareSuites(traces: Trajectory[]): boolean {
  if (traces.length <= 1) return true;
  const firstSuites = traces[0]?.suites;
  if (!firstSuites) return false;
  const firstNames = firstSuites.map((s) => s.name).sort().join(",");
  for (let i = 1; i < traces.length; i++) {
    const s = traces[i]?.suites;
    if (!s) return false;
    const names = s.map((suite) => suite.name).sort().join(",");
    if (names !== firstNames) return false;
  }
  return true;
}

export function ProgressCurves({ traces, colorIndices }: ProgressCurvesProps) {
  const [hoveredTrace, setHoveredTrace] = useState<number | undefined>(undefined);
  const [hoveredPoint, setHoveredPoint] = useState<HoveredPoint | undefined>(undefined);

  const maxDuration = useMemo(() => computeMaxDuration(traces), [traces]);
  const xTicks = useMemo(() => getXTicks(maxDuration), [maxDuration]);
  const shareSuites = useMemo(() => tracesShareSuites(traces), [traces]);

  /** Find nearest commit to mouse position within a trace */
  const handleMouseMove = useCallback(
    (event: React.MouseEvent<SVGSVGElement>) => {
      const svg = event.currentTarget;
      const rect = svg.getBoundingClientRect();
      const mouseX = ((event.clientX - rect.left) / rect.width) * VIEW_WIDTH;
      const mouseY = ((event.clientY - rect.top) / rect.height) * VIEW_HEIGHT;

      // Only respond within the plot area
      if (
        mouseX < MARGIN.left ||
        mouseX > VIEW_WIDTH - MARGIN.right ||
        mouseY < MARGIN.top ||
        mouseY > MARGIN.top + PLOT_HEIGHT
      ) {
        setHoveredPoint(undefined);
        setHoveredTrace(undefined);
        return;
      }

      let best: HoveredPoint | undefined;
      let bestDist = Infinity;

      for (let ti = 0; ti < traces.length; ti++) {
        const trace = traces[ti];
        if (!trace) continue;
        const total = trace.totalTests;
        for (let ci = 0; ci < trace.commits.length; ci++) {
          const commit = trace.commits[ci];
          if (!commit) continue;
          const cx = toX(commit.minutesElapsed, maxDuration);
          const cy = toYPct(toPct(commit.totalPassed, total));
          const dist = Math.sqrt((mouseX - cx) ** 2 + (mouseY - cy) ** 2);
          if (dist < bestDist && dist < 30) {
            bestDist = dist;
            best = { traceIndex: ti, commitIndex: ci, commit, totalTests: total, cx, cy };
          }
        }
      }

      if (best) {
        setHoveredPoint(best);
        setHoveredTrace(best.traceIndex);
      } else {
        setHoveredPoint(undefined);
        setHoveredTrace(undefined);
      }
    },
    [traces, maxDuration],
  );

  const handleMouseLeave = useCallback(() => {
    setHoveredPoint(undefined);
    setHoveredTrace(undefined);
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {/* SVG Chart */}
      <div className="rounded border border-envoi-border bg-envoi-bg p-3">
        <svg
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          className="w-full"
          style={{ fontFamily: T.mono }}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
        >
          {/* Grid lines */}
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

          {/* Y axis labels (left: percentage) */}
          {Y_PCT_TICKS.map((pct) => (
            <text
              key={`y-pct-${pct}`}
              x={MARGIN.left - 8}
              y={toYPct(pct) + 3}
              textAnchor="end"
              style={{ fontSize: "9px", fill: T.textDim }}
            >
              {`${pct}%`}
            </text>
          ))}

          {/* Y axis labels (right: absolute count — only when all traces share same totalTests) */}
          {traces.length > 0 &&
            traces.every((t) => t.totalTests === traces[0]?.totalTests) &&
            Y_PCT_TICKS.map((pct) => {
              const total = traces[0]?.totalTests ?? 0;
              return (
                <text
                  key={`y-abs-${pct}`}
                  x={VIEW_WIDTH - MARGIN.right + 8}
                  y={toYPct(pct) + 3}
                  textAnchor="start"
                  style={{ fontSize: "9px", fill: T.textDim }}
                >
                  {Math.round((pct / 100) * total)}
                </text>
              );
            })}

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
                d={buildAreaPath(trace.commits, trace.totalTests, maxDuration)}
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
                d={buildLinePath(trace.commits, trace.totalTests, maxDuration)}
                fill="none"
                stroke={color.line}
                strokeWidth={hoveredTrace === traceIndex ? 2.5 : 1.5}
                opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
                style={{ pointerEvents: "none" }}
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
                  cy={toYPct(toPct(commit.totalPassed, trace.totalTests))}
                  r={3}
                  fill={T.red}
                  stroke={T.bg}
                  strokeWidth={1}
                  opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.2 : 1}
                  style={{ pointerEvents: "none" }}
                />
              ));
          })}

          {/* Endpoint score labels (show % instead of raw count) */}
          {traces.map((trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            const lastCommit = trace.commits[trace.commits.length - 1];
            if (!lastCommit) return undefined;
            const pct = toPct(lastCommit.totalPassed, trace.totalTests);
            return (
              <text
                key={`label-${trace.id}`}
                x={toX(lastCommit.minutesElapsed, maxDuration) + 6}
                y={toYPct(pct) + 3}
                style={{
                  fontSize: "9px",
                  fill: color.line,
                  fontWeight: 700,
                }}
                opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
              >
                {`${color.label}: ${pct.toFixed(1)}%`}
              </text>
            );
          })}

          {/* Hover crosshair lines + tooltip */}
          {hoveredPoint && (() => {
            const { traceIndex, commit, totalTests, cx, cy } = hoveredPoint;
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
            if (!color) return undefined;
            const pct = toPct(commit.totalPassed, totalTests);

            return (
              <g style={{ pointerEvents: "none" }}>
                {/* Dotted vertical line to X axis */}
                <line
                  x1={cx}
                  y1={cy}
                  x2={cx}
                  y2={MARGIN.top + PLOT_HEIGHT}
                  stroke={color.line}
                  strokeWidth={1}
                  strokeDasharray="3,3"
                  opacity={0.5}
                />
                {/* Dotted horizontal line to Y axis */}
                <line
                  x1={MARGIN.left}
                  y1={cy}
                  x2={cx}
                  y2={cy}
                  stroke={color.line}
                  strokeWidth={1}
                  strokeDasharray="3,3"
                  opacity={0.5}
                />
                {/* Highlight dot */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={4}
                  fill={color.line}
                  stroke={T.bg}
                  strokeWidth={2}
                />
                {/* Tooltip background + text above the point */}
                <rect
                  x={cx - 44}
                  y={cy - 32}
                  width={88}
                  height={24}
                  rx={4}
                  fill="#0a0a0a"
                  opacity={0.9}
                />
                <text
                  x={cx}
                  y={cy - 16}
                  textAnchor="middle"
                  style={{ fontSize: "9px", fill: "#ffffff", fontWeight: 600 }}
                >
                  {`${pct.toFixed(1)}% (${commit.totalPassed}/${totalTests})`}
                </text>
              </g>
            );
          })()}
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
          const traceSuites = trace.suites ?? [];

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
                    {formatPercent(lastCommit?.totalPassed ?? 0, trace.totalTests)}
                    <span className="text-envoi-text-dim">
                      {" "}({lastCommit?.totalPassed ?? 0}/{trace.totalTests})
                    </span>
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[10px] text-envoi-text-dim">
                    <Hash size={10} />
                    Peak
                  </span>
                  <span className="text-[11px] font-semibold">
                    {formatPercent(maxPassed, trace.totalTests)}
                    <span className="text-envoi-text-dim">
                      {" "}({maxPassed}/{trace.totalTests})
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

              {/* Per-suite mini bars — only if suites are shared across all selected traces */}
              {shareSuites && traceSuites.length > 0 && (
                <div className="mt-2 space-y-1 border-t border-envoi-border-light pt-2">
                  {traceSuites.map((suite) => {
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
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
