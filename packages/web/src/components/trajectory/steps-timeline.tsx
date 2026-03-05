/**
 * Gantt-style timeline for the STEPS tab.
 * Renders a horizontal bar chart where each step's width is proportional
 * to its duration. Stays fixed above the scrollable step list.
 *
 * Click a bar to select/scroll to that step in the list below.
 * Hover shows a tooltip with step type, index, and duration.
 *
 * Time labels are rendered as HTML below the SVG to avoid distortion.
 */

"use client";

import { useMemo, useCallback } from "react";
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

/** Minimum bar width as percentage so tiny steps stay clickable */
const MIN_BAR_PCT = 0.3;

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
  xPct: number;
  widthPct: number;
  color: string;
  label: string;
  durationMs: number;
};

/** Compute bar positions as percentages of total width */
function computeBarLayout(steps: Step[]): { bars: BarLayout[]; totalDuration: number } {
  const durations: number[] = [];
  for (const step of steps) {
    durations.push(step.durationMs ?? DEFAULT_DURATION_MS);
  }

  const totalDuration = durations.reduce((sum, duration) => sum + duration, 0);
  if (totalDuration === 0) {
    return { bars: [], totalDuration: 0 };
  }

  const bars: BarLayout[] = [];
  let cumulativePct = 0;

  for (let index = 0; index < steps.length; index++) {
    const step = steps[index];
    if (!step) {
      continue;
    }
    const durationMs = durations[index] ?? DEFAULT_DURATION_MS;
    const rawPct = (durationMs / totalDuration) * 100;
    const barPct = Math.max(rawPct, MIN_BAR_PCT);

    bars.push({
      step,
      filteredIndex: index,
      xPct: cumulativePct,
      widthPct: barPct,
      color: STEP_COLORS[step.type],
      label: STEP_LABELS[step.type],
      durationMs,
    });

    cumulativePct += barPct;
  }

  return { bars, totalDuration };
}

/** Compute evenly-spaced time axis ticks as percentages */
function computeTimeTicks(totalDuration: number): { ms: number; pct: number }[] {
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

  const ticks: { ms: number; pct: number }[] = [];
  let ms = 0;
  while (ms <= totalDuration) {
    ticks.push({
      ms,
      pct: (ms / totalDuration) * 100,
    });
    ms += interval;
  }

  // Always include the total duration as the last tick
  const lastTick = ticks[ticks.length - 1];
  if (!lastTick || lastTick.ms < totalDuration) {
    ticks.push({ ms: totalDuration, pct: 100 });
  }

  return ticks;
}

/** Gantt-style timeline chart for agent steps */
export function StepsTimeline({ steps, selectedStepIndex, onSelectStep }: StepsTimelineProps) {
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
      {/* Bar chart — uses percentage-based positioning via HTML divs */}
      <div className="relative" style={{ height: 20 }}>
        {/* Grid lines at time ticks */}
        {ticks.map((tick) => (
          <div
            key={tick.ms}
            className="absolute top-0 h-full"
            style={{
              left: `${tick.pct}%`,
              width: 1,
              backgroundColor: T.borderLight,
            }}
          />
        ))}

        {/* Step bars */}
        {bars.map((bar) => {
          const isSelected = selectedStepIndex === bar.filteredIndex;
          const truncatedSummary = bar.step.summary.length > 80
            ? bar.step.summary.slice(0, 80) + "..."
            : bar.step.summary;
          const tooltipText = `${bar.label} #${bar.step.index + 1} — ${formatTimeLabel(bar.durationMs)}${truncatedSummary ? `\n${truncatedSummary}` : ""}`;

          return (
            <div
              key={`${bar.step.index}-${bar.filteredIndex}`}
              className="absolute top-0 h-full"
              style={{
                left: `${bar.xPct}%`,
                width: `${bar.widthPct}%`,
                backgroundColor: bar.step.isError ? T.red : bar.color,
                opacity: isSelected ? 0.7 : 0.35,
                cursor: "pointer",
                borderBottom: isSelected ? `2px solid ${T.accent}` : undefined,
              }}
              title={tooltipText}
              onClick={() => handleBarClick(bar.filteredIndex)}
            />
          );
        })}
      </div>

      {/* Time axis labels — rendered as HTML so they don't distort */}
      <div className="relative" style={{ height: 16, marginTop: 2 }}>
        {ticks.map((tick) => (
          <span
            key={tick.ms}
            className="absolute"
            style={{
              left: `${tick.pct}%`,
              transform: tick.ms === 0 ? "none" : "translateX(-50%)",
              fontSize: 10,
              color: T.textDim,
              fontFamily: T.mono,
              whiteSpace: "nowrap",
            }}
          >
            {formatTimeLabel(tick.ms)}
          </span>
        ))}
      </div>
    </div>
  );
}
