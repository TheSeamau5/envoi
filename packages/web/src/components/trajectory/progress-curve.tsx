/**
 * Interactive SVG progress curve for the trajectory detail view.
 * Renders commit dots on a curve; clicking a dot selects that commit.
 * Selected dot is larger with dark stroke. Curve is filled up to selection,
 * faded after. Red dots for regressions, gold dots for milestones.
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

/** Chart layout constants */
const VIEW_WIDTH = 600;
const VIEW_HEIGHT = 130;
const MARGIN = { top: 14, right: 14, bottom: 14, left: 14 };
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
): string {
  return commits
    .map((commit, pointIndex) => {
      const cmd = pointIndex === 0 ? "M" : "L";
      const xPos = toX(pointIndex, commits.length);
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

export function ProgressCurve({
  commits,
  selectedIndex,
  onSelect,
  activeSuite,
}: ProgressCurveProps) {
  const yMax = getYMax(activeSuite);

  return (
    <div className="w-full px-[14px] pt-[10px]">
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="w-full"
        style={{ height: 130, fontFamily: T.mono }}
      >
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
          d={buildLinePath(commits.slice(0, selectedIndex + 1), activeSuite, yMax)}
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

          const dotRadius = isSelected ? 5 : 3;

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
                {`Turn ${commit.turn}: ${getYValue(commit, activeSuite)} passed (${commit.delta >= 0 ? "+" : ""}${commit.delta})`}
              </title>
            </circle>
          );
        })}
      </svg>
    </div>
  );
}
