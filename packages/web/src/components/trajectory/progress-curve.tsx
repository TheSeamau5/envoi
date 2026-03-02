/**
 * Interactive SVG progress curve for the trajectory detail view.
 * Renders commit dots on a curve; clicking a dot selects that commit.
 * Selected dot is larger with dark stroke. Curve is filled up to selection,
 * faded after. Red dots for regressions, gold dots for milestones.
 * X-axis shows elapsed time labels (minutes or hours).
 *
 * Optional reasoning density overlay (toggled via metric selector): a
 * translucent area chart on a secondary Y-axis showing reasoning tokens
 * per commit. Hidden by default to keep the primary view clean.
 *
 * The SVG uses a viewBox and scales uniformly via the default
 * preserveAspectRatio — dots stay circular, text stays proportional.
 *
 * Client component — handles click interaction.
 */

"use client";

import { useState } from "react";
import type { Commit, Suite } from "@/lib/types";
import { T } from "@/lib/tokens";
import { TOTAL_TESTS as DEFAULT_TOTAL_TESTS, SUITES as DEFAULT_SUITES } from "@/lib/constants";

type ProgressCurveProps = {
  commits: Commit[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  activeSuite: string;
  suites?: Suite[];
  totalTests?: number;
};

/**
 * Chart layout constants.
 * The viewBox defines a coordinate system; the SVG scales uniformly to fill
 * its container width. A wider viewBox = more horizontal room for data, and
 * the rendered height adjusts proportionally.
 */
const VIEW_WIDTH = 800;
const VIEW_HEIGHT = 260;
const MARGIN = { top: 14, right: 44, bottom: 28, left: 48 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Color for the reasoning density overlay */
const REASONING_COLOR = "rgba(37, 99, 235, 0.15)";
const REASONING_STROKE = "rgba(37, 99, 235, 0.35)";

/** Get Y-axis max based on active suite filter */
function getYMax(activeSuite: string, suites: Suite[], totalTests: number): number {
  if (activeSuite === "all") {
    return totalTests;
  }
  const suite = suites.find((suiteItem) => suiteItem.name === activeSuite);
  return suite ? suite.total : totalTests;
}

/** Get Y value for a commit based on active suite */
function getYValue(commit: Commit, activeSuite: string): number {
  if (activeSuite === "all") {
    return commit.totalPassed;
  }
  return commit.suiteState[activeSuite] ?? 0;
}

/** Map commit index to X pixel position */
function toX(commitIndex: number, totalCommits: number): number {
  if (totalCommits <= 1) {
    return MARGIN.left + PLOT_WIDTH / 2;
  }
  return MARGIN.left + (commitIndex / (totalCommits - 1)) * PLOT_WIDTH;
}

/** Map value to Y pixel position */
function toY(value: number, yMax: number): number {
  if (yMax === 0) {
    return MARGIN.top + PLOT_HEIGHT;
  }
  return MARGIN.top + PLOT_HEIGHT - (value / yMax) * PLOT_HEIGHT;
}

/** Map a time in minutes to an X pixel position (linear interpolation across duration) */
function timeToX(minutes: number, totalMinutes: number): number {
  if (totalMinutes <= 0) {
    return MARGIN.left;
  }
  const ratio = Math.min(1, minutes / totalMinutes);
  return MARGIN.left + ratio * PLOT_WIDTH;
}

/** Map a reasoning token value to Y pixel position using the secondary axis */
function toReasoningY(value: number, maxReasoningTokens: number): number {
  if (maxReasoningTokens === 0) {
    return MARGIN.top + PLOT_HEIGHT;
  }
  return MARGIN.top + PLOT_HEIGHT - (value / maxReasoningTokens) * PLOT_HEIGHT;
}

/** Build SVG line path from commits */
function buildLinePath(
  commits: Commit[],
  activeSuite: string,
  yMax: number,
  totalCommits?: number,
): string {
  const total = totalCommits ?? commits.length;
  return commits
    .map((commit, pointIndex) => {
      const cmd = pointIndex === 0 ? "M" : "L";
      const xPos = toX(pointIndex, total);
      const yPos = toY(getYValue(commit, activeSuite), yMax);
      return `${cmd}${xPos.toFixed(1)},${yPos.toFixed(1)}`;
    })
    .join(" ");
}

/** Build SVG area path (filled under the curve) up to a given index */
function buildAreaPath(
  commits: Commit[],
  endIndex: number,
  activeSuite: string,
  yMax: number,
): string {
  const subset = commits.slice(0, endIndex + 1);
  if (subset.length === 0) {
    return "";
  }

  const lineSegments = subset
    .map((commit, pointIndex) => {
      const cmd = pointIndex === 0 ? "M" : "L";
      const xPos = toX(pointIndex, commits.length);
      const yPos = toY(getYValue(commit, activeSuite), yMax);
      return `${cmd}${xPos.toFixed(1)},${yPos.toFixed(1)}`;
    })
    .join(" ");

  const bottomY = toY(0, yMax);
  const lastX = toX(endIndex, commits.length);
  const firstX = toX(0, commits.length);

  return `${lineSegments} L${lastX.toFixed(1)},${bottomY.toFixed(1)} L${firstX.toFixed(1)},${bottomY.toFixed(1)} Z`;
}

/** Compute reasoning tokens for a commit by summing tokensUsed from reasoning steps */
function getReasoningTokens(commit: Commit): number {
  let total = 0;
  for (const step of commit.steps) {
    if (step.type === "reasoning" && step.tokensUsed) {
      total += step.tokensUsed;
    }
  }
  return total;
}

/** Build SVG area path for reasoning density overlay */
function buildReasoningAreaPath(
  commits: Commit[],
  maxReasoningTokens: number,
): string {
  if (commits.length === 0 || maxReasoningTokens === 0) {
    return "";
  }

  const lineSegments = commits
    .map((commit, pointIndex) => {
      const cmd = pointIndex === 0 ? "M" : "L";
      const xPos = toX(pointIndex, commits.length);
      const yPos = toReasoningY(getReasoningTokens(commit), maxReasoningTokens);
      return `${cmd}${xPos.toFixed(1)},${yPos.toFixed(1)}`;
    })
    .join(" ");

  const bottomY = MARGIN.top + PLOT_HEIGHT;
  const lastX = toX(commits.length - 1, commits.length);
  const firstX = toX(0, commits.length);

  return `${lineSegments} L${lastX.toFixed(1)},${bottomY.toFixed(1)} L${firstX.toFixed(1)},${bottomY.toFixed(1)} Z`;
}

/** Build SVG line path for reasoning density */
function buildReasoningLinePath(
  commits: Commit[],
  maxReasoningTokens: number,
): string {
  if (maxReasoningTokens === 0) {
    return "";
  }
  return commits
    .map((commit, pointIndex) => {
      const cmd = pointIndex === 0 ? "M" : "L";
      const xPos = toX(pointIndex, commits.length);
      const yPos = toReasoningY(getReasoningTokens(commit), maxReasoningTokens);
      return `${cmd}${xPos.toFixed(1)},${yPos.toFixed(1)}`;
    })
    .join(" ");
}

/** Generate Y-axis tick values */
function getYTicks(yMax: number): number[] {
  return Array.from({ length: 5 }, (_, tickIdx) => Math.round((tickIdx / 4) * yMax));
}

/** Format minutes as a time label — "0" for zero, then "30m", "1h", "2h", etc. */
function formatTimeLabel(minutes: number): string {
  if (minutes === 0) {
    return "0";
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (mins === 0) {
    return `${hours}h`;
  }
  return `${hours}h${mins}m`;
}

/** Generate sensible X-axis time ticks based on total duration */
function getXTimeTicks(totalMinutes: number): number[] {
  if (totalMinutes <= 0) {
    return [0];
  }

  let intervalMinutes: number;
  if (totalMinutes <= 60) {
    intervalMinutes = 15;
  } else if (totalMinutes <= 180) {
    intervalMinutes = 30;
  } else if (totalMinutes <= 360) {
    intervalMinutes = 60;
  } else {
    intervalMinutes = 120;
  }

  const ticks: number[] = [0];
  for (let tick = intervalMinutes; tick < totalMinutes; tick += intervalMinutes) {
    ticks.push(tick);
  }
  // Include the end if it's not already close to the last tick
  const lastTick = ticks[ticks.length - 1];
  if (lastTick !== undefined && totalMinutes - lastTick > intervalMinutes * 0.4) {
    ticks.push(totalMinutes);
  }
  return ticks;
}

/** Format token count as compact string (e.g., "12K", "1.5M") */
function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(0)}K`;
  }
  return String(tokens);
}

/** Metric options for the chart overlay */
type MetricMode = "none" | "reasoning";

/** Interactive SVG progress curve with optional reasoning density overlay */
export function ProgressCurve({
  commits,
  selectedIndex,
  onSelect,
  activeSuite,
  suites: suitesProp,
  totalTests: totalTestsProp,
}: ProgressCurveProps) {
  const [metricMode, setMetricMode] = useState<MetricMode>("none");

  const effectiveSuites = suitesProp ?? DEFAULT_SUITES;
  const effectiveTotal = totalTestsProp ?? DEFAULT_TOTAL_TESTS;

  /** Safeguard: ensure y-axis is never less than the max observed data point.
   *  This handles legacy trajectories where suites may reflect partial eval results. */
  let maxObserved = 0;
  for (const commit of commits) {
    const value = getYValue(commit, activeSuite);
    if (value > maxObserved) {
      maxObserved = value;
    }
  }
  const yMax = Math.max(getYMax(activeSuite, effectiveSuites, effectiveTotal), maxObserved);
  const yTicks = getYTicks(yMax);
  const lastCommit = commits[commits.length - 1];
  const totalMinutes = lastCommit?.minutesElapsed ?? 0;
  const xTimeTicks = getXTimeTicks(totalMinutes);

  /** Compute max reasoning tokens across all commits for secondary Y-axis scaling */
  let maxReasoningTokens = 0;
  let hasReasoningData = false;
  for (const commit of commits) {
    const reasoningTokens = getReasoningTokens(commit);
    if (reasoningTokens > 0) {
      hasReasoningData = true;
      if (reasoningTokens > maxReasoningTokens) {
        maxReasoningTokens = reasoningTokens;
      }
    }
  }

  const showReasoning = metricMode === "reasoning" && hasReasoningData;

  return (
    <div className="w-full px-[6px] pt-[6px] pb-[2px]">
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="w-full"
        style={{ fontFamily: T.mono }}
      >
        {/* Horizontal grid lines */}
        {yTicks.map((tick) => (
          <line
            key={`y-grid-${tick}`}
            x1={MARGIN.left}
            y1={toY(tick, yMax)}
            x2={VIEW_WIDTH - MARGIN.right}
            y2={toY(tick, yMax)}
            stroke={T.borderLight}
            strokeWidth={0.5}
          />
        ))}

        {/* Vertical grid lines at time ticks */}
        {xTimeTicks.map((tickMinutes) => {
          const xPos = timeToX(tickMinutes, totalMinutes);
          return (
            <line
              key={`x-grid-${tickMinutes}`}
              x1={xPos}
              y1={MARGIN.top}
              x2={xPos}
              y2={MARGIN.top + PLOT_HEIGHT}
              stroke={T.borderLight}
              strokeWidth={0.5}
            />
          );
        })}

        {/* Y axis labels — left: absolute count */}
        {yTicks.map((tick) => (
          <text
            key={`y-label-${tick}`}
            x={MARGIN.left - 8}
            y={toY(tick, yMax) + 3.5}
            textAnchor="end"
            style={{ fontSize: "9px", fill: T.textDim }}
          >
            {tick}
          </text>
        ))}

        {/* Y axis labels — right: percentage */}
        {yTicks.map((tick) => (
          <text
            key={`y-pct-${tick}`}
            x={VIEW_WIDTH - MARGIN.right + 8}
            y={toY(tick, yMax) + 3.5}
            textAnchor="start"
            style={{ fontSize: "9px", fill: T.textDim }}
          >
            {`${Math.round((tick / yMax) * 100)}%`}
          </text>
        ))}

        {/* X axis labels — time */}
        {xTimeTicks.map((tickMinutes) => {
          const xPos = timeToX(tickMinutes, totalMinutes);
          return (
            <text
              key={`x-label-${tickMinutes}`}
              x={xPos}
              y={MARGIN.top + PLOT_HEIGHT + 16}
              textAnchor="middle"
              style={{ fontSize: "10px", fill: T.textDim }}
            >
              {formatTimeLabel(tickMinutes)}
            </text>
          );
        })}

        {/* Reasoning density overlay — only shown when metric toggle is active */}
        {showReasoning && (
          <>
            <path
              d={buildReasoningAreaPath(commits, maxReasoningTokens)}
              fill={REASONING_COLOR}
            />
            <path
              d={buildReasoningLinePath(commits, maxReasoningTokens)}
              fill="none"
              stroke={REASONING_STROKE}
              strokeWidth={0.8}
            />
          </>
        )}

        {/* Filled area up to selection */}
        <path
          d={buildAreaPath(commits, selectedIndex, activeSuite, yMax)}
          fill={T.accentBg}
        />

        {/* Faded area after selection */}
        {selectedIndex < commits.length - 1 && (
          <path
            d={buildAreaPath(commits, commits.length - 1, activeSuite, yMax)}
            fill={T.borderLight}
            opacity={0.4}
          />
        )}

        {/* Full line (faded portion after selection) */}
        <path
          d={buildLinePath(commits, activeSuite, yMax)}
          fill="none"
          stroke={T.border}
          strokeWidth={1.2}
          opacity={0.4}
        />

        {/* Active line up to selection */}
        <path
          d={buildLinePath(commits.slice(0, selectedIndex + 1), activeSuite, yMax, commits.length)}
          fill="none"
          stroke={T.accent}
          strokeWidth={1.2}
        />

        {/* Percentage label above selected commit */}
        {(() => {
          const commit = commits[selectedIndex];
          if (!commit) {
            return undefined;
          }
          const xPos = toX(selectedIndex, commits.length);
          const yPos = toY(getYValue(commit, activeSuite), yMax);
          const pct = Math.round((getYValue(commit, activeSuite) / yMax) * 100);
          return (
            <text
              x={xPos}
              y={yPos - 12}
              textAnchor="middle"
              style={{ fontSize: "10px", fill: T.accent, fontWeight: 700 }}
            >
              {pct}%
            </text>
          );
        })()}

        {/* Commit dots */}
        {commits.map((commit, dotIndex) => {
          const xPos = toX(dotIndex, commits.length);
          const yPos = toY(getYValue(commit, activeSuite), yMax);
          const isSelected = dotIndex === selectedIndex;
          const isBeforeSelection = dotIndex <= selectedIndex;

          const dotColor = commit.isRegression
            ? T.red
            : commit.isMilestone
              ? T.gold
              : isBeforeSelection
                ? T.accent
                : T.textDim;

          const dotRadius = isSelected ? 5 : 3;

          return (
            <circle
              key={commit.index}
              cx={xPos}
              cy={yPos}
              r={dotRadius}
              fill={dotColor}
              stroke={isSelected ? T.text : "none"}
              strokeWidth={isSelected ? 1.5 : 0}
              style={{ cursor: "pointer" }}
              onClick={() => onSelect(dotIndex)}
            >
              <title>
                {`Turn ${commit.turn} (${formatTimeLabel(commit.minutesElapsed)}): ${getYValue(commit, activeSuite)} passed (${commit.delta >= 0 ? "+" : ""}${commit.delta})${showReasoning ? ` | Reasoning: ${formatTokenCount(getReasoningTokens(commit))} tokens` : ""}`}
              </title>
            </circle>
          );
        })}

        {/* Legend — only when reasoning overlay is active */}
        {showReasoning && (
          <g>
            <rect
              x={MARGIN.left + 4}
              y={MARGIN.top + 2}
              width={8}
              height={8}
              rx={1}
              fill={REASONING_COLOR}
              stroke={REASONING_STROKE}
              strokeWidth={0.5}
            />
            <text
              x={MARGIN.left + 16}
              y={MARGIN.top + 9}
              style={{ fontSize: "8px", fill: T.textDim }}
            >
              Reasoning density (tokens/commit)
            </text>
          </g>
        )}
      </svg>

      {/* Metric toggle — only shown when reasoning data exists */}
      {hasReasoningData && (
        <div className="flex items-center gap-[6px] px-[6px] pt-[4px]">
          <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
            Overlay:
          </span>
          <button
            onClick={() => setMetricMode(metricMode === "none" ? "reasoning" : "none")}
            className={`rounded-full px-[8px] py-[1px] text-[11px] font-semibold transition-colors ${
              metricMode === "reasoning"
                ? "bg-blue-100 text-blue-700"
                : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light"
            }`}
          >
            Reasoning density
          </button>
        </div>
      )}
    </div>
  );
}
