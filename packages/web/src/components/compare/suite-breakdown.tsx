/**
 * Suite Breakdown — per-suite mini SVG charts + comparison table.
 * Client component: hover interactions on SVG charts.
 *
 * Grid of 4 mini SVG charts (one per suite), each showing overlaid progress curves
 * for the selected traces. Below: a table with per-suite progress bars.
 */

"use client";

import { useState } from "react";
import type { Trajectory, Commit, Suite } from "@/lib/types";
import { TRACE_COLORS, T, SUITE_COLORS } from "@/lib/tokens";
import { SUITES as DEFAULT_SUITES, MAX_DURATION } from "@/lib/constants";
import { formatPercent } from "@/lib/utils";

type SuiteBreakdownProps = {
  traces: Trajectory[];
  /** Stable color index for each trace (parallel to `traces` array) */
  colorIndices?: number[];
  suites?: Suite[];
};

/** Mini chart layout constants */
const MINI_WIDTH = 220;
const MINI_HEIGHT = 120;
const MINI_MARGIN = { top: 14, right: 8, bottom: 20, left: 32 };
const MINI_PLOT_W = MINI_WIDTH - MINI_MARGIN.left - MINI_MARGIN.right;
const MINI_PLOT_H = MINI_HEIGHT - MINI_MARGIN.top - MINI_MARGIN.bottom;

/** Map minutes to X in mini chart */
function miniToX(minutes: number): number {
  return MINI_MARGIN.left + (minutes / MAX_DURATION) * MINI_PLOT_W;
}

/** Map suite passed count to Y in mini chart */
function miniToY(passed: number, suiteTotal: number): number {
  return MINI_MARGIN.top + MINI_PLOT_H - (passed / suiteTotal) * MINI_PLOT_H;
}

/** Build SVG line path for a trace within a single suite */
function buildSuiteLinePath(commits: Commit[], suiteName: string, suiteTotal: number): string {
  return commits
    .map((commit, pointIdx) => {
      const passed = commit.suiteState[suiteName] ?? 0;
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${miniToX(commit.minutesElapsed).toFixed(1)},${miniToY(passed, suiteTotal).toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG area path for a trace within a single suite */
function buildSuiteAreaPath(commits: Commit[], suiteName: string, suiteTotal: number): string {
  if (commits.length === 0) return "";
  const firstCommit = commits[0]!;
  const lastCommit = commits[commits.length - 1]!;
  const lineSegments = commits
    .map((commit, pointIdx) => {
      const passed = commit.suiteState[suiteName] ?? 0;
      const cmd = pointIdx === 0 ? "M" : "L";
      return `${cmd}${miniToX(commit.minutesElapsed).toFixed(1)},${miniToY(passed, suiteTotal).toFixed(1)}`;
    })
    .join(" ");
  const bottomRight = `L${miniToX(lastCommit.minutesElapsed).toFixed(1)},${miniToY(0, suiteTotal).toFixed(1)}`;
  const bottomLeft = `L${miniToX(firstCommit.minutesElapsed).toFixed(1)},${miniToY(0, suiteTotal).toFixed(1)}`;
  return `${lineSegments} ${bottomRight} ${bottomLeft} Z`;
}

/** Single mini chart for one suite */
function MiniSuiteChart({
  suiteName,
  suiteTotal,
  traces,
  colorIndices,
  hoveredTrace,
  onHover,
}: {
  suiteName: string;
  suiteTotal: number;
  traces: Trajectory[];
  colorIndices?: number[];
  hoveredTrace: number | undefined;
  onHover: (index: number | undefined) => void;
}) {
  const suiteColor = SUITE_COLORS[suiteName];
  const yTicks = [0, Math.round(suiteTotal / 2), suiteTotal];

  return (
    <div className="rounded border border-envoi-border bg-envoi-bg p-2">
      <div className="mb-1 flex items-center gap-2 px-1">
        <span
          className="h-[6px] w-[6px] rounded-full"
          style={{ background: suiteColor?.color ?? T.textMuted }}
        />
        <span className="text-[10px] font-semibold text-envoi-text">{suiteName}</span>
        <span className="text-[9px] text-envoi-text-dim">{suiteTotal} tests</span>
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
            style={{ fontSize: "7px", fill: T.textDim }}
          >
            {tick}
          </text>
        ))}

        {/* X axis labels */}
        <text
          x={MINI_MARGIN.left}
          y={MINI_HEIGHT - 4}
          textAnchor="start"
          style={{ fontSize: "7px", fill: T.textDim }}
        >
          0h
        </text>
        <text
          x={MINI_WIDTH - MINI_MARGIN.right}
          y={MINI_HEIGHT - 4}
          textAnchor="end"
          style={{ fontSize: "7px", fill: T.textDim }}
        >
          8h
        </text>

        {/* Area fills */}
        {traces.map((trace, traceIndex) => {
          const traceColor = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length]!;
          return (
            <path
              key={`area-${trace.id}`}
              d={buildSuiteAreaPath(trace.commits, suiteName, suiteTotal)}
              fill={traceColor.fill}
              opacity={hoveredTrace !== undefined && hoveredTrace !== traceIndex ? 0.3 : 1}
            />
          );
        })}

        {/* Lines */}
        {traces.map((trace, traceIndex) => {
          const traceColor = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length]!;
          return (
            <path
              key={`line-${trace.id}`}
              d={buildSuiteLinePath(trace.commits, suiteName, suiteTotal)}
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
          const traceColor = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length]!;
          const lastCommit = trace.commits[trace.commits.length - 1];
          if (!lastCommit) return undefined;
          const passed = lastCommit.suiteState[suiteName] ?? 0;
          return (
            <text
              key={`endpoint-${trace.id}`}
              x={miniToX(lastCommit.minutesElapsed) + 3}
              y={miniToY(passed, suiteTotal) + 2}
              style={{ fontSize: "7px", fill: traceColor.line, fontWeight: 700 }}
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

export function SuiteBreakdown({ traces, colorIndices, suites: suitesProp }: SuiteBreakdownProps) {
  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const [hoveredTrace, setHoveredTrace] = useState<number | undefined>(undefined);

  return (
    <div className="flex flex-col gap-4">
      {/* Mini charts grid */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {effectiveSuites.map((suite) => (
          <MiniSuiteChart
            key={suite.name}
            suiteName={suite.name}
            suiteTotal={suite.total}
            traces={traces}
            colorIndices={colorIndices}
            hoveredTrace={hoveredTrace}
            onHover={setHoveredTrace}
          />
        ))}
      </div>

      {/* Comparison table */}
      <div className="rounded border border-envoi-border bg-envoi-bg">
        {/* Table header */}
        <div className="flex items-center border-b border-envoi-border bg-envoi-surface px-[14px] py-[10px]">
          <span className="min-w-[100px] text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Suite
          </span>
          <span className="min-w-[60px] text-right text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Total
          </span>
          {traces.map((_trace, traceIndex) => {
            const color = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length]!;
            return (
              <span
                key={`col-${traceIndex}`}
                className="flex min-w-[160px] flex-1 items-center gap-[6px] pl-4 text-[10px] font-semibold uppercase tracking-[0.08em]"
                style={{ color: color.line }}
              >
                <span
                  className="flex h-[14px] w-[14px] items-center justify-center rounded text-[8px] font-bold text-white"
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
        {effectiveSuites.map((suite) => {
          const suiteColor = SUITE_COLORS[suite.name];
          return (
            <div
              key={suite.name}
              className="flex items-center border-b border-envoi-border-light px-[14px] py-[10px] transition-colors hover:bg-envoi-surface"
            >
              {/* Suite name */}
              <span className="flex min-w-[100px] items-center gap-2 text-[11px] font-medium text-envoi-text">
                <span
                  className="h-[6px] w-[6px] rounded-full"
                  style={{ background: suiteColor?.color ?? T.textMuted }}
                />
                {suite.name}
              </span>

              {/* Total */}
              <span className="min-w-[60px] text-right text-[11px] text-envoi-text-muted">
                {suite.total}
              </span>

              {/* Per-trace cells with progress bar */}
              {traces.map((trace, traceIndex) => {
                const lastCommit = trace.commits[trace.commits.length - 1];
                const passed = lastCommit?.suiteState[suite.name] ?? 0;
                const pct = (passed / suite.total) * 100;
                const traceColor = TRACE_COLORS[(colorIndices?.[traceIndex] ?? traceIndex) % TRACE_COLORS.length]!;

                return (
                  <div
                    key={`cell-${trace.id}-${suite.name}`}
                    className="flex min-w-[160px] flex-1 items-center gap-3 pl-4"
                  >
                    {/* Progress bar */}
                    <div className="h-[5px] w-[60px] rounded-full bg-envoi-border-light">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${pct}%`,
                          background: traceColor.line,
                        }}
                      />
                    </div>
                    {/* Values */}
                    <span className="text-[11px] font-semibold" style={{ color: traceColor.line }}>
                      {passed}
                    </span>
                    <span className="text-[9px] text-envoi-text-dim">
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
