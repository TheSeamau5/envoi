/**
 * Pareto frontier scatter plot — custom SVG visualization showing
 * cost (tokens) vs score (pass rate) for all trajectories.
 *
 * Each dot represents one trajectory, colored by model. The Pareto frontier
 * line connects non-dominated points (no other trajectory achieves the same
 * progress at lower cost).
 *
 * Features:
 * - Model-color legend with toggle visibility
 * - Hover tooltip with trajectory details
 * - Pareto frontier line highlighting
 * - Environment filter
 */

"use client";

import { useState, useMemo } from "react";
import type { ParetoPoint } from "@/lib/types";
import { T } from "@/lib/tokens";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type ParetoScatterProps = {
  points: ParetoPoint[];
  environments: string[];
};

const VIEW_WIDTH = 700;
const VIEW_HEIGHT = 360;
const MARGIN = { top: 20, right: 30, bottom: 40, left: 60 };
const PLOT_WIDTH = VIEW_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = VIEW_HEIGHT - MARGIN.top - MARGIN.bottom;

/** Palette for model colors (max 8 distinct models) */
const MODEL_COLORS = [
  "#0a0a0a",
  "#059669",
  "#dc2626",
  "#7c3aed",
  "#2563eb",
  "#ea580c",
  "#0891b2",
  "#a17a08",
];

/** Map X pixel position from token count */
function toX(tokens: number, maxTokens: number): number {
  if (maxTokens <= 0) {
    return MARGIN.left;
  }
  return MARGIN.left + (tokens / maxTokens) * PLOT_WIDTH;
}

/** Map Y pixel position from pass rate (0-1) */
function toY(passRate: number): number {
  return MARGIN.top + PLOT_HEIGHT - passRate * PLOT_HEIGHT;
}

/** Format token count as compact label */
function formatTokenLabel(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(0)}K`;
  }
  return String(tokens);
}

/**
 * Compute the Pareto frontier from a set of points.
 * Sort by cost ascending, keep only points where score strictly increases.
 * Returns the subset of points on the frontier.
 */
function computeParetoFrontier(points: ParetoPoint[]): ParetoPoint[] {
  const sorted = [...points].sort((pointA, pointB) => pointA.totalTokens - pointB.totalTokens);
  const frontier: ParetoPoint[] = [];
  let bestScore = -1;

  for (const point of sorted) {
    if (point.passRate > bestScore) {
      frontier.push(point);
      bestScore = point.passRate;
    }
  }

  return frontier;
}

/** Pareto frontier scatter plot with model coloring and hover tooltips */
export function ParetoScatter({ points, environments }: ParetoScatterProps) {
  const [selectedEnv, setSelectedEnv] = useState<string>("all");

  const filteredPoints = useMemo(() => {
    if (selectedEnv === "all") {
      return points;
    }
    return points.filter((point) => point.environment === selectedEnv);
  }, [points, selectedEnv]);

  /** Assign colors to models */
  const modelColorMap = useMemo(() => {
    const models = Array.from(new Set(points.map((point) => point.model))).sort();
    const colorMap = new Map<string, string>();
    for (const [modelIdx, model] of models.entries()) {
      colorMap.set(model, MODEL_COLORS[modelIdx % MODEL_COLORS.length] ?? T.textDim);
    }
    return colorMap;
  }, [points]);

  /** Compute axis ranges */
  const maxTokens = useMemo(() => {
    let max = 0;
    for (const point of filteredPoints) {
      if (point.totalTokens > max) {
        max = point.totalTokens;
      }
    }
    return max > 0 ? max * 1.1 : 1_000_000;
  }, [filteredPoints]);

  /** Compute Pareto frontier */
  const frontier = useMemo(() => computeParetoFrontier(filteredPoints), [filteredPoints]);

  /** Build frontier line path */
  const frontierPath = useMemo(() => {
    if (frontier.length < 2) {
      return "";
    }
    return frontier
      .map((point, pointIdx) => {
        const cmd = pointIdx === 0 ? "M" : "L";
        return `${cmd}${toX(point.totalTokens, maxTokens).toFixed(1)},${toY(point.passRate).toFixed(1)}`;
      })
      .join(" ");
  }, [frontier, maxTokens]);

  /** Y-axis ticks: 0%, 25%, 50%, 75%, 100% */
  const yTicks = [0, 0.25, 0.5, 0.75, 1.0];

  /** X-axis ticks */
  const xTicks = useMemo(() => {
    const ticks: number[] = [];
    const interval = maxTokens / 4;
    for (let tick = 0; tick <= maxTokens; tick += interval) {
      ticks.push(Math.round(tick));
    }
    return ticks;
  }, [maxTokens]);

  if (filteredPoints.length === 0) {
    return (
      <div className="flex items-center justify-center py-[40px] text-[13px] text-envoi-text-dim">
        No trajectory data for Pareto analysis
      </div>
    );
  }

  return (
    <div>
      {/* Environment filter */}
      <div className="mb-3 flex items-center gap-[6px]">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-envoi-text-dim">
          Environment:
        </span>
        <button
          onClick={() => setSelectedEnv("all")}
          className={`rounded-full px-[8px] py-[2px] text-[12px] font-semibold transition-colors ${
            selectedEnv === "all"
              ? "bg-envoi-text text-white"
              : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light"
          }`}
        >
          All
        </button>
        {environments.map((env) => (
          <button
            key={env}
            onClick={() => setSelectedEnv(env)}
            className={`rounded-full px-[8px] py-[2px] text-[12px] font-semibold transition-colors ${
              selectedEnv === env
                ? "bg-envoi-text text-white"
                : "bg-envoi-surface text-envoi-text-dim hover:bg-envoi-border-light"
            }`}
          >
            {env}
          </button>
        ))}
      </div>

      {/* Scatter plot */}
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        className="w-full"
        style={{ maxWidth: VIEW_WIDTH, fontFamily: T.mono }}
      >
        {/* Grid lines */}
        {yTicks.map((tick) => (
          <line
            key={`y-grid-${tick}`}
            x1={MARGIN.left}
            y1={toY(tick)}
            x2={VIEW_WIDTH - MARGIN.right}
            y2={toY(tick)}
            stroke={T.borderLight}
            strokeWidth={0.5}
          />
        ))}
        {xTicks.map((tick) => (
          <line
            key={`x-grid-${tick}`}
            x1={toX(tick, maxTokens)}
            y1={MARGIN.top}
            x2={toX(tick, maxTokens)}
            y2={MARGIN.top + PLOT_HEIGHT}
            stroke={T.borderLight}
            strokeWidth={0.5}
          />
        ))}

        {/* Y-axis labels */}
        {yTicks.map((tick) => (
          <text
            key={`y-label-${tick}`}
            x={MARGIN.left - 8}
            y={toY(tick) + 4}
            textAnchor="end"
            style={{ fontSize: "10px", fill: T.textDim }}
          >
            {(tick * 100).toFixed(0)}%
          </text>
        ))}

        {/* X-axis labels */}
        {xTicks.map((tick) => (
          <text
            key={`x-label-${tick}`}
            x={toX(tick, maxTokens)}
            y={MARGIN.top + PLOT_HEIGHT + 18}
            textAnchor="middle"
            style={{ fontSize: "10px", fill: T.textDim }}
          >
            {formatTokenLabel(tick)}
          </text>
        ))}

        {/* Axis labels */}
        <text
          x={VIEW_WIDTH / 2}
          y={VIEW_HEIGHT - 4}
          textAnchor="middle"
          style={{ fontSize: "11px", fill: T.textMuted }}
        >
          Total tokens (cost proxy)
        </text>
        <text
          x={14}
          y={VIEW_HEIGHT / 2}
          textAnchor="middle"
          transform={`rotate(-90, 14, ${VIEW_HEIGHT / 2})`}
          style={{ fontSize: "11px", fill: T.textMuted }}
        >
          Pass rate
        </text>

        {/* Frontier band — light fill under the frontier line */}
        {frontier.length >= 2 && (
          <path
            d={`${frontierPath} L${toX(frontier[frontier.length - 1]?.totalTokens ?? 0, maxTokens).toFixed(1)},${toY(0).toFixed(1)} L${toX(frontier[0]?.totalTokens ?? 0, maxTokens).toFixed(1)},${toY(0).toFixed(1)} Z`}
            fill="rgba(249, 115, 22, 0.06)"
          />
        )}

        {/* Frontier line */}
        {frontierPath && (
          <path
            d={frontierPath}
            fill="none"
            stroke={T.accent}
            strokeWidth={1.5}
            strokeDasharray="4 2"
          />
        )}

        {/* Data points */}
        {filteredPoints.map((point) => {
          const color = modelColorMap.get(point.model) ?? T.textDim;
          const xPos = toX(point.totalTokens, maxTokens);
          const yPos = toY(point.passRate);

          return (
            <Tooltip key={point.trajectoryId}>
              <TooltipTrigger asChild>
                <circle
                  cx={xPos}
                  cy={yPos}
                  r={4}
                  fill={color}
                  opacity={0.75}
                  className="cursor-pointer"
                />
              </TooltipTrigger>
              <TooltipContent>
                <div className="text-[12px]">
                  <div className="font-semibold">{point.trajectoryId}</div>
                  <div>{point.model}</div>
                  <div>Score: {point.passed}/{point.total} ({(point.passRate * 100).toFixed(1)}%)</div>
                  <div>Tokens: {formatTokenLabel(point.totalTokens)}</div>
                </div>
              </TooltipContent>
            </Tooltip>
          );
        })}
      </svg>

      {/* Model legend */}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {Array.from(modelColorMap.entries()).map(([model, color]) => (
          <div key={model} className="flex items-center gap-[6px]">
            <div
              className="h-[8px] w-[8px] rounded-full"
              style={{ background: color }}
            />
            <span className="text-[11px] text-envoi-text-muted">{model}</span>
          </div>
        ))}
        {frontier.length >= 2 && (
          <div className="flex items-center gap-[6px]">
            <div
              className="h-px w-[16px]"
              style={{ borderTop: `1.5px dashed ${T.accent}` }}
            />
            <span className="text-[11px] text-envoi-text-muted">Pareto frontier</span>
          </div>
        )}
      </div>
    </div>
  );
}
