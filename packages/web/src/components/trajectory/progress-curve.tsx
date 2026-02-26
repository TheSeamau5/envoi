/**
 * Interactive SVG progress curve for the trajectory detail view.
 * Renders commit dots on a curve; clicking a dot selects that commit.
 * Selected dot is larger with dark stroke. Curve is filled up to selection,
 * faded after. Red dots for regressions, gold dots for milestones.
 * X-axis shows elapsed time labels (minutes or hours).
 *
 * Client component — handles click interaction.
 */

"use client";

import type { Commit } from "@/lib/types";
import { T } from "@/lib/tokens";
import { TOTAL_TESTS, SUITES } from "@/lib/constants";

type ProgressCurveProps = {
  commits: Commit[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  activeSuite: string;
};

/** Chart layout constants — wide margins for axis labels, full-width plot area */
const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 320;
const MARGIN = { top: 16, right: 48, bottom: 32, left: 52 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Get Y-axis max based on active suite filter */
function getYMax(activeSuite: string): number {
  if (activeSuite === "all") return TOTAL_TESTS;
  const suite = SUITES.find((suiteItem) => suiteItem.name === activeSuite);
  return suite ? suite.total : TOTAL_TESTS;
}

/** Get Y value for a commit based on active suite */
function getYValue(commit: Commit, activeSuite: string): number {
  if (activeSuite === "all") return commit.totalPassed;
  return commit.suiteState[activeSuite] ?? 0;
}

/** Map commit index to X pixel position */
function toX(commitIndex: number, totalCommits: number): number {
  if (totalCommits <= 1) return MARGIN.left + PLOT_WIDTH / 2;
  return MARGIN.left + (commitIndex / (totalCommits - 1)) * PLOT_WIDTH;
}

/** Map value to Y pixel position */
function toY(value: number, yMax: number): number {
  if (yMax === 0) return MARGIN.top + PLOT_HEIGHT;
  return MARGIN.top + PLOT_HEIGHT - (value / yMax) * PLOT_HEIGHT;
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
  if (subset.length === 0) return "";

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

/** Generate Y-axis tick values */
function getYTicks(yMax: number): number[] {
  return Array.from({ length: 5 }, (_, tickIdx) => Math.round((tickIdx / 4) * yMax));
}

/** Format minutes as a time label (e.g., "0m", "30m", "1h", "2h 30m") */
function formatTimeLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (mins === 0) return `${hours}h`;
  return `${hours}h${mins}m`;
}

/** Generate sensible X-axis time ticks based on total duration */
function getXTicks(commits: Commit[]): { minutes: number; commitIndex: number }[] {
  if (commits.length === 0) return [];

  const lastCommit = commits[commits.length - 1]!;
  const totalMinutes = lastCommit.minutesElapsed;

  // Choose tick interval based on total duration
  let intervalMinutes: number;
  if (totalMinutes <= 60) {
    intervalMinutes = 10;
  } else if (totalMinutes <= 180) {
    intervalMinutes = 30;
  } else if (totalMinutes <= 360) {
    intervalMinutes = 60;
  } else {
    intervalMinutes = 120;
  }

  const ticks: { minutes: number; commitIndex: number }[] = [];

  // Always include the start
  ticks.push({ minutes: 0, commitIndex: 0 });

  // Add intermediate ticks
  for (let tickMinutes = intervalMinutes; tickMinutes < totalMinutes; tickMinutes += intervalMinutes) {
    // Find the closest commit to this time
    let closestIndex = 0;
    let closestDist = Infinity;
    for (let idx = 0; idx < commits.length; idx++) {
      const dist = Math.abs(commits[idx]!.minutesElapsed - tickMinutes);
      if (dist < closestDist) {
        closestDist = dist;
        closestIndex = idx;
      }
    }
    ticks.push({ minutes: tickMinutes, commitIndex: closestIndex });
  }

  // Always include the end
  if (totalMinutes > 0) {
    ticks.push({ minutes: totalMinutes, commitIndex: commits.length - 1 });
  }

  return ticks;
}

export function ProgressCurve({
  commits,
  selectedIndex,
  onSelect,
  activeSuite,
}: ProgressCurveProps) {
  const yMax = getYMax(activeSuite);
  const yTicks = getYTicks(yMax);
  const xTicks = getXTicks(commits);

  return (
    <div className="w-full px-[6px] pt-[10px]">
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="w-full"
        style={{ height: 320, fontFamily: T.mono }}
        preserveAspectRatio="none"
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
        {xTicks.map((tick) => {
          const xPos = toX(tick.commitIndex, commits.length);
          return (
            <line
              key={`x-grid-${tick.minutes}`}
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
        {xTicks.map((tick) => {
          const xPos = toX(tick.commitIndex, commits.length);
          return (
            <text
              key={`x-label-${tick.minutes}`}
              x={xPos}
              y={MARGIN.top + PLOT_HEIGHT + 18}
              textAnchor="middle"
              style={{ fontSize: "9px", fill: T.textDim }}
            >
              {formatTimeLabel(tick.minutes)}
            </text>
          );
        })}

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
          strokeWidth={1.5}
          opacity={0.4}
        />

        {/* Active line up to selection */}
        <path
          d={buildLinePath(commits.slice(0, selectedIndex + 1), activeSuite, yMax, commits.length)}
          fill="none"
          stroke={T.accent}
          strokeWidth={1.5}
        />

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

          const dotRadius = isSelected ? 6 : 3.5;

          return (
            <circle
              key={commit.index}
              cx={xPos}
              cy={yPos}
              r={dotRadius}
              fill={dotColor}
              stroke={isSelected ? T.text : "none"}
              strokeWidth={isSelected ? 2 : 0}
              style={{ cursor: "pointer" }}
              onClick={() => onSelect(dotIndex)}
            >
              <title>
                {`Turn ${commit.turn} (${formatTimeLabel(commit.minutesElapsed)}): ${getYValue(commit, activeSuite)} passed (${commit.delta >= 0 ? "+" : ""}${commit.delta})`}
              </title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}
