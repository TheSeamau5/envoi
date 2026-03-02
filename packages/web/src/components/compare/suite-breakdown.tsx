/**
 * Suite Breakdown — per-suite mini SVG charts + comparison table.
 * Client component: hover interactions on SVG charts.
 *
 * When traces span multiple environments, suites are grouped by environment.
 * Each environment section only shows traces from that environment, preventing
 * nonsensical mixing of cross-environment data.
 */

"use client";

import { useState, useMemo } from "react";
import type { Trajectory, Commit, Suite } from "@/lib/types";
import { TRACE_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { SUITES as DEFAULT_SUITES } from "@/lib/constants";
import { formatPercent, computeMaxDuration } from "@/lib/utils";

type SuiteBreakdownProps = {
  traces: Trajectory[];
  /** Stable color index for each trace (parallel to `traces` array) */
  colorIndices?: number[];
  suites?: Suite[];
};

/** Environment group: traces and suites belonging to one environment */
type EnvGroup = {
  environment: string;
  suites: Suite[];
  traces: Trajectory[];
  /** Color indices mapped from parent (for TRACE_COLORS lookup) */
  colorIndices: number[];
};

/** Mini chart layout constants */
const MINI_WIDTH = 220;
const MINI_HEIGHT = 120;
const MINI_MARGIN = { top: 14, right: 8, bottom: 20, left: 32 };
const MINI_PLOT_W = MINI_WIDTH - MINI_MARGIN.left - MINI_MARGIN.right;
const MINI_PLOT_H = MINI_HEIGHT - MINI_MARGIN.top - MINI_MARGIN.bottom;

/** Map minutes to X in mini chart */
function miniToX(minutes: number, maxDuration: number): number {
  if (maxDuration === 0) {
    return MINI_MARGIN.left;
  }
  return MINI_MARGIN.left + (minutes / maxDuration) * MINI_PLOT_W;
}

/** Map suite passed count to Y in mini chart */
function miniToY(passed: number, suiteTotal: number): number {
  return MINI_MARGIN.top + MINI_PLOT_H - (passed / suiteTotal) * MINI_PLOT_H;
}

/** Build SVG line path for a trace within a single suite */
function buildSuiteLinePath(commits: Commit[], suiteName: string, suiteTotal: number, maxDuration: number): string {
  return commits
    .map((commit, pointIdx) => {
      const passed = commit.suiteState[suiteName] ?? 0;
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${miniToX(commit.minutesElapsed, maxDuration).toFixed(1)},${miniToY(passed, suiteTotal).toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG area path for a trace within a single suite */
function buildSuiteAreaPath(commits: Commit[], suiteName: string, suiteTotal: number, maxDuration: number): string {
  if (commits.length === 0) {
    return "";
  }
  const firstCommit = commits[0];
  const lastCommit = commits[commits.length - 1];
  if (!firstCommit || !lastCommit) {
    return "";
  }
  const lineSegments = commits
    .map((commit, pointIdx) => {
      const passed = commit.suiteState[suiteName] ?? 0;
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${miniToX(commit.minutesElapsed, maxDuration).toFixed(1)},${miniToY(passed, suiteTotal).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${miniToX(lastCommit.minutesElapsed, maxDuration).toFixed(1)},${miniToY(0, suiteTotal).toFixed(1)}`;
  const bottomLeft = `L${miniToX(firstCommit.minutesElapsed, maxDuration).toFixed(1)},${miniToY(0, suiteTotal).toFixed(1)}`;
  return `${lineSegments} ${bottomRight} ${bottomLeft} Z`;
}

/** Group traces by environment, assigning suites to each group */
function groupByEnvironment(
  traces: Trajectory[],
  allSuites: Suite[],
  colorIndices?: number[],
): EnvGroup[] {
  const envIndices = new Map<string, number[]>();
  const envSuiteNames = new Map<string, Set<string>>();

  for (let traceIdx = 0; traceIdx < traces.length; traceIdx++) {
    const trace = traces[traceIdx];
    if (!trace) {
      continue;
    }
    const env = trace.environment || "unknown";

    const indices = envIndices.get(env);
    if (indices) {
      indices.push(traceIdx);
    } else {
      envIndices.set(env, [traceIdx]);
    }

    let suiteNames = envSuiteNames.get(env);
    if (!suiteNames) {
      suiteNames = new Set();
      envSuiteNames.set(env, suiteNames);
    }
    if (trace.suites) {
      for (const suite of trace.suites) {
        suiteNames.add(suite.name);
      }
    }
  }

  return [...envIndices.entries()]
    .sort(([envA], [envB]) => envA.localeCompare(envB))
    .map(([environment, parentIndices]) => ({
      environment,
      suites: allSuites
        .filter((suite) => envSuiteNames.get(environment)?.has(suite.name) ?? false)
        .sort((suiteA, suiteB) => suiteA.name.localeCompare(suiteB.name)),
      traces: parentIndices.map((idx) => traces[idx] as Trajectory),
      colorIndices: parentIndices.map((idx) => colorIndices?.[idx] ?? idx),
    }));
}

/** Single mini chart for one suite */
function MiniSuiteChart({
  suiteName,
  suiteTotal,
  traces,
  colorIndices,
  hoveredTrace,
  onHover,
  maxDuration,
}: {
  suiteName: string;
  suiteTotal: number;
  traces: Trajectory[];
  colorIndices: number[];
  hoveredTrace?: number;
  onHover: (index?: number) => void;
  maxDuration: number;
}) {
  const suiteColor = SUITE_COLORS[suiteName];
  const yTicks = [...new Set([0, Math.round(suiteTotal / 2), suiteTotal])];

  return (
    <div className="rounded border border-envoi-border bg-envoi-bg p-2">
      <div className="mb-1 flex items-center gap-2 px-1">
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: suiteColor?.color ?? T.textMuted }}
        />
        <span className="text-[12px] font-semibold text-envoi-text">{suiteName}</span>
        <span className="text-[13px] text-envoi-text-dim">{suiteTotal} tests</span>
      </div>
      <svg
        viewBox={`0 0 ${MINI_WIDTH} ${MINI_HEIGHT}`}
        className="w-full"
        style={{ fontFamily: T.mono }}
      >
        {/* Grid lines */}
        {yTicks.map((tick) => (
          <line
            key={`grid-${tick}`}
            x1={MINI_MARGIN.left}
            y1={miniToY(tick, suiteTotal)}
            x2={MINI_WIDTH - MINI_MARGIN.right}
            y2={miniToY(tick, suiteTotal)}
            stroke={T.borderLight}
            strokeWidth={0.5}
          />
        ))}

        {/* Y labels */}
        {yTicks.map((tick) => (
          <text
            key={`ylabel-${tick}`}
            x={MINI_MARGIN.left - 4}
            y={miniToY(tick, suiteTotal) + 3}
            textAnchor="end"
            style={{ fontSize: "9px", fill: T.textDim }}
          >
            {tick}
          </text>
        ))}

        {/* X axis labels */}
        <text
          x={MINI_MARGIN.left}
          y={MINI_HEIGHT - 4}
          textAnchor="start"
          style={{ fontSize: "9px", fill: T.textDim }}
        >
          0
        </text>
        <text
          x={MINI_WIDTH - MINI_MARGIN.right}
          y={MINI_HEIGHT - 4}
          textAnchor="end"
          style={{ fontSize: "9px", fill: T.textDim }}
        >
          {maxDuration < 60 ? `${maxDuration}m` : `${Math.round(maxDuration / 60)}h`}
        </text>

        {/* Area fills */}
        {traces.map((trace, traceIndex) => {
          const traceColor = TRACE_COLORS[(colorIndices[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
          if (!traceColor) {
            return undefined;
          }
          return (
            <path
              key={`area-${trace.id}`}
              d={buildSuiteAreaPath(trace.commits, suiteName, suiteTotal, maxDuration)}
              fill={traceColor.fill}
              opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
            />
          );
        })}

        {/* Lines */}
        {traces.map((trace, traceIndex) => {
          const traceColor = TRACE_COLORS[(colorIndices[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
          if (!traceColor) {
            return undefined;
          }
          return (
            <path
              key={`line-${trace.id}`}
              d={buildSuiteLinePath(trace.commits, suiteName, suiteTotal, maxDuration)}
              fill="none"
              stroke={traceColor.line}
              strokeWidth={hoveredTrace === traceIndex ? 2 : 1.2}
              opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
              onMouseEnter={() => onHover(traceIndex)}
              onMouseLeave={() => onHover(undefined)}
              style={{ cursor: "pointer" }}
            />
          );
        })}

        {/* Endpoint labels */}
        {traces.map((trace, traceIndex) => {
          const traceColor = TRACE_COLORS[(colorIndices[traceIndex] ?? traceIndex) % TRACE_COLORS.length];
          if (!traceColor) {
            return undefined;
          }
          const lastCommit = trace.commits[trace.commits.length - 1];
          if (!lastCommit) {
            return undefined;
          }
          const passed = lastCommit.suiteState[suiteName] ?? 0;
          return (
            <text
              key={`endpoint-${trace.id}`}
              x={miniToX(lastCommit.minutesElapsed, maxDuration) + 3}
              y={miniToY(passed, suiteTotal) + 2}
              style={{ fontSize: "9px", fill: traceColor.line, fontWeight: 700 }}
              opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
            >
              {traceColor.label}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/**
 * Environment suite section — renders mini charts + comparison table
 * for one environment's suites and traces. Manages its own hover state
 * so hovering in one environment doesn't affect another.
 */
function EnvSuiteSection({
  envGroup,
  maxDuration,
}: {
  envGroup: EnvGroup;
  maxDuration: number;
}) {
  const [hoveredTrace, setHoveredTrace] = useState<number>();

  return (
    <div className="flex flex-col gap-4">
      {/* Mini charts grid */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {envGroup.suites.map((suite) => (
          <MiniSuiteChart
            key={suite.name}
            suiteName={suite.name}
            suiteTotal={suite.total}
            traces={envGroup.traces}
            colorIndices={envGroup.colorIndices}
            hoveredTrace={hoveredTrace}
            onHover={setHoveredTrace}
            maxDuration={maxDuration}
          />
        ))}
      </div>

      {/* Comparison table */}
      <div className="rounded border border-envoi-border bg-envoi-bg">
        {/* Table header */}
        <div className="flex items-center border-b border-envoi-border bg-envoi-surface px-3.5 py-2.5">
          <span className="min-w-25 text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Suite
          </span>
          <span className="min-w-15 text-right text-[12px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Total
          </span>
          {envGroup.traces.map((trace, envTraceIdx) => {
            const colorIdx = envGroup.colorIndices[envTraceIdx] ?? envTraceIdx;
            const color = TRACE_COLORS[colorIdx % TRACE_COLORS.length];
            if (!color) {
              return undefined;
            }
            return (
              <span
                key={`col-${trace.id}`}
                className="flex min-w-40 flex-1 items-center gap-1.5 pl-4 text-[12px] font-semibold uppercase tracking-[0.08em]"
                style={{ color: color.line }}
              >
                <span
                  className="flex h-3.5 w-3.5 items-center justify-center rounded text-[8px] font-bold text-white"
                  style={{ background: color.line }}
                >
                  {color.label}
                </span>
                Trace {color.label}
              </span>
            );
          })}
        </div>

        {/* Rows per suite */}
        {envGroup.suites.map((suite) => {
          const suiteColor = SUITE_COLORS[suite.name];
          return (
            <div
              key={suite.name}
              className="flex items-center border-b border-envoi-border-light px-3.5 py-2.5 transition-colors hover:bg-envoi-surface"
            >
              {/* Suite name */}
              <span className="flex min-w-25 items-center gap-2 text-[13px] font-medium text-envoi-text">
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: suiteColor?.color ?? T.textMuted }}
                />
                {suite.name}
              </span>

              {/* Total */}
              <span className="min-w-15 text-right text-[13px] text-envoi-text-muted">
                {suite.total}
              </span>

              {/* Per-trace cells with progress bar */}
              {envGroup.traces.map((trace, envTraceIdx) => {
                const colorIdx = envGroup.colorIndices[envTraceIdx] ?? envTraceIdx;
                const lastCommit = trace.commits[trace.commits.length - 1];
                const passed = lastCommit?.suiteState[suite.name] ?? 0;
                const pct = (passed / suite.total) * 100;
                const traceColor = TRACE_COLORS[colorIdx % TRACE_COLORS.length];
                if (!traceColor) {
                  return undefined;
                }

                return (
                  <div
                    key={`cell-${trace.id}-${suite.name}`}
                    className="flex min-w-40 flex-1 items-center gap-3 pl-4"
                  >
                    {/* Progress bar */}
                    <div className="h-1.25 w-15 rounded-full bg-envoi-border-light">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${pct}%`,
                          background: traceColor.line,
                        }}
                      />
                    </div>
                    {/* Values */}
                    <span className="text-[13px] font-semibold" style={{ color: traceColor.line }}>
                      {passed}
                    </span>
                    <span className="text-[13px] text-envoi-text-dim">
                      {formatPercent(passed, suite.total)}
                    </span>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SuiteBreakdown({ traces, colorIndices, suites: suitesProp }: SuiteBreakdownProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const maxDuration = useMemo(() => computeMaxDuration(traces), [traces]);

  const envGroups = useMemo(
    () => groupByEnvironment(traces, effectiveSuites, colorIndices),
    [traces, effectiveSuites, colorIndices],
  );

  return (
    <div className="flex flex-col gap-6">
      {envGroups.map((envGroup) => (
        <div key={envGroup.environment}>
          {/* Environment header — only show when multiple environments present */}
          {envGroups.length > 1 && (
            <div className="mb-3 border-b border-envoi-border pb-1.5">
              <span className="text-[12px] font-bold uppercase tracking-[0.08em] text-envoi-text-dim">
                {envGroup.environment}
              </span>
              <span className="ml-2 text-[12px] text-envoi-text-dim">
                ({envGroup.traces.length} traces · {envGroup.suites.length} suites)
              </span>
            </div>
          )}
          <EnvSuiteSection envGroup={envGroup} maxDuration={maxDuration} />
        </div>
      ))}
    </div>
  );
}
