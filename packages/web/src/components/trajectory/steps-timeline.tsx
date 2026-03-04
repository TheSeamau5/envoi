/**
 * Gantt-style timeline for the STEPS tab.
 * Renders a horizontal bar chart where each step's width is proportional
 * to its duration. Stays fixed above the scrollable step list.
 *
 * Click a bar to select/scroll to that step in the list below.
 * Hover shows a tooltip with step type, index, and duration.
 */

"use client";

import { useMemo, useRef, useCallback } from "react";
import type { Step } from "@/lib/types";
import { T } from "@/lib/tokens";

/** Step type → color mapping (mirrors STEP_CONFIG in steps-panel.tsx) */
const STEP_COLORS: Record<Step["type"], string> = {
  reasoning: T.stepReasoning,
  file_read: T.stepRead,
  file_write: T.stepWrite,
  tool_call: T.stepTool,
  test_run: T.stepTest,
  mcp_call: T.stepMcp,
  text: T.stepText,
  spawn: T.stepSpawn,
  message: T.stepMessage,
};

/** Step type → short label for tooltips */
const STEP_LABELS: Record<Step["type"], string> = {
  reasoning: "REASONING",
  file_read: "READ",
  file_write: "WRITE",
  tool_call: "TOOL",
  test_run: "TEST",
  mcp_call: "MCP",
  text: "TEXT",
  spawn: "SPAWN",
  message: "MESSAGE",
};

/** Minimum duration assigned to steps without timing data */
const DEFAULT_DURATION_MS = 500;

/** Minimum bar width in SVG units so tiny steps stay clickable */
const MIN_BAR_WIDTH = 3;

/** SVG layout constants */
const SVG_WIDTH = 1000;
const BAR_Y = 6;
const BAR_HEIGHT = 36;
const AXIS_Y = BAR_Y + BAR_HEIGHT + 14;
const SVG_HEIGHT = AXIS_Y + 4;
const BAR_GAP = 1;

type StepsTimelineProps = {
  steps: Step[];
  selectedStepIndex?: number;
  onSelectStep: (stepIndex: number) => void;
};

/** Format milliseconds into a compact label for axis ticks */
function formatTimeLabel(ms: number): string {
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  if (remainingSeconds > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${minutes}m`;
}

/** Precomputed bar layout for a single step */
type BarLayout = {
  step: Step;
  filteredIndex: number;
  x: number;
  width: number;
  color: string;
  label: string;
  durationMs: number;
  cumulativeMs: number;
};

/** Compute bar positions from step durations */
function computeBarLayout(steps: Step[]): { bars: BarLayout[]; totalDuration: number } {
  const durations: number[] = [];
  for (const step of steps) {
    durations.push(step.durationMs ?? DEFAULT_DURATION_MS);
  }

  const totalDuration = durations.reduce((sum, duration) => sum + duration, 0);
  if (totalDuration === 0) {
    return { bars: [], totalDuration: 0 };
  }

  const usableWidth = SVG_WIDTH - (steps.length - 1) * BAR_GAP;
  const bars: BarLayout[] = [];
  let cumulativeMs = 0;
  let cumulativeX = 0;

  for (let index = 0; index < steps.length; index++) {
    const step = steps[index];
    if (!step) {
      continue;
    }
    const durationMs = durations[index] ?? DEFAULT_DURATION_MS;
    const rawWidth = (durationMs / totalDuration) * usableWidth;
    const barWidth = Math.max(rawWidth, MIN_BAR_WIDTH);

    bars.push({
      step,
      filteredIndex: index,
      x: cumulativeX,
      width: barWidth,
      color: STEP_COLORS[step.type],
      label: STEP_LABELS[step.type],
      durationMs,
      cumulativeMs,
    });

    cumulativeMs += durationMs;
    cumulativeX += barWidth + BAR_GAP;
  }

  return { bars, totalDuration };
}

/** Compute evenly-spaced time axis ticks */
function computeTimeTicks(totalDuration: number): { ms: number; x: number }[] {
  if (totalDuration === 0) {
    return [];
  }

  const targetTickCount = 5;
  const rawInterval = totalDuration / targetTickCount;

  const niceIntervals = [100, 250, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000];
  let interval = niceIntervals[niceIntervals.length - 1] ?? 600000;
  for (const candidate of niceIntervals) {
    if (candidate >= rawInterval) {
      interval = candidate;
      break;
    }
  }

  const ticks: { ms: number; x: number }[] = [];
  let ms = 0;
  while (ms <= totalDuration) {
    ticks.push({
      ms,
      x: (ms / totalDuration) * SVG_WIDTH,
    });
    ms += interval;
  }

  return ticks;
}

/** Gantt-style timeline chart for agent steps */
export function StepsTimeline({ steps, selectedStepIndex, onSelectStep }: StepsTimelineProps) {
  const svgRef = useRef<SVGSVGElement>(null);

  const { bars, totalDuration } = useMemo(() => computeBarLayout(steps), [steps]);
  const ticks = useMemo(() => computeTimeTicks(totalDuration), [totalDuration]);

  const handleBarClick = useCallback(
    (filteredIndex: number) => {
      onSelectStep(filteredIndex);
    },
    [onSelectStep],
  );

  if (bars.length === 0) {
    return null;
  }

  return (
    <div className="shrink-0 border-b border-envoi-border px-3.5 py-2">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: 56, display: "block" }}
      >
        {/* Time axis ticks */}
        {ticks.map((tick) => (
          <g key={tick.ms}>
            <line
              x1={tick.x}
              y1={BAR_Y}
              x2={tick.x}
              y2={BAR_Y + BAR_HEIGHT}
              stroke={T.borderLight}
              strokeWidth={1}
            />
            <text
              x={tick.x}
              y={AXIS_Y}
              fill={T.textDim}
              fontSize={22}
              fontFamily={T.mono}
              textAnchor={tick.ms === 0 ? "start" : "middle"}
            >
              {formatTimeLabel(tick.ms)}
            </text>
          </g>
        ))}

        {/* Step bars */}
        {bars.map((bar) => {
          const isSelected = selectedStepIndex === bar.filteredIndex;
          const truncatedSummary = bar.step.summary.length > 80
            ? bar.step.summary.slice(0, 80) + "..."
            : bar.step.summary;
          const tooltipText = `${bar.label} #${bar.step.index + 1} — ${formatTimeLabel(bar.durationMs)}${truncatedSummary ? `\n${truncatedSummary}` : ""}`;

          return (
            <g
              key={`${bar.step.index}-${bar.filteredIndex}`}
              onClick={() => handleBarClick(bar.filteredIndex)}
              style={{ cursor: "pointer" }}
            >
              <rect
                x={bar.x}
                y={BAR_Y}
                width={bar.width}
                height={BAR_HEIGHT}
                rx={3}
                ry={3}
                fill={`${bar.color}${isSelected ? "40" : "25"}`}
                stroke={isSelected ? T.accent : bar.step.isError ? T.red : "transparent"}
                strokeWidth={isSelected ? 3 : bar.step.isError ? 2 : 0}
              >
                <title>{tooltipText}</title>
              </rect>
              {/* Colored top accent line */}
              <rect
                x={bar.x}
                y={BAR_Y}
                width={bar.width}
                height={3}
                rx={1.5}
                ry={1.5}
                fill={bar.color}
                style={{ pointerEvents: "none" }}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
