/**
 * Difficulty heatmap — SVG color matrix showing best pass rates per
 * (category, model), segmented by environment with section headers.
 *
 * Features:
 * - Color interpolation: red (0%) → yellow (50%) → green (100%)
 * - Frontier band: dashed border on cells in the 35-65% range (optimal training signal)
 * - Click-through: clicking a cell navigates to the trajectory list filtered by model
 * - Tooltip: shows model name, best pass rate percentage, and trajectory count
 *
 * Client component for hover/click interactions.
 */

"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import type { DifficultyCell } from "@/lib/types";
import { T } from "@/lib/tokens";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type DifficultyHeatmapProps = {
  cells: DifficultyCell[];
  project: string;
};

const CELL_W = 160;
const CELL_H = 34;
const GAP = 2;
const LABEL_W = 200;
const LABEL_H = 44;
const ENV_HEADER_H = 38;
const ENV_GAP = 20;

/** Minimum pass rate for the frontier training band */
const FRONTIER_MIN = 0.35;

/** Maximum pass rate for the frontier training band */
const FRONTIER_MAX = 0.65;

/** Interpolate between red (0%) → yellow (50%) → green (100%) */
function passRateColor(rate: number): string {
  const clamped = Math.max(0, Math.min(1, rate));
  if (clamped < 0.5) {
    /** Red → Yellow */
    const ratio = clamped / 0.5;
    const red = 239;
    const green = Math.round(68 + (179 - 68) * ratio);
    const blue = Math.round(68 * (1 - ratio));
    return `rgb(${red},${green},${blue})`;
  }
  /** Yellow → Green */
  const ratio = (clamped - 0.5) / 0.5;
  const red = Math.round(234 - (234 - 34) * ratio);
  const green = Math.round(179 + (197 - 179) * ratio);
  const blue = Math.round(8 + (94 - 8) * ratio);
  return `rgb(${red},${green},${blue})`;
}

/** Choose text color based on background brightness */
function textColor(rate: number): string {
  return rate > 0.3 && rate < 0.7 ? T.text : "#ffffff";
}

/** Whether a pass rate falls in the frontier training band */
function isFrontier(rate: number): boolean {
  return rate >= FRONTIER_MIN && rate <= FRONTIER_MAX;
}

function splitModelLabel(model: string): {
  agent: string;
  model?: string;
} {
  const slashIndex = model.indexOf("/");
  if (slashIndex < 0) {
    return { agent: model };
  }
  return {
    agent: model.slice(0, slashIndex),
    model: model.slice(slashIndex + 1),
  };
}

type EnvironmentGroup = {
  environment: string;
  categories: string[];
};

type CategoryLayout = {
  category: string;
  cellY: number;
};

type EnvironmentLayout = {
  environment: string;
  sectionY: number;
  categories: CategoryLayout[];
};

/** Difficulty heatmap with click-through navigation and frontier band highlighting */
export function DifficultyHeatmap({ cells, project }: DifficultyHeatmapProps) {
  const router = useRouter();

  const { envGroups, models, cellMap } = useMemo(() => {
    const modelSet = new Set<string>();
    const map = new Map<string, DifficultyCell>();
    /** Group categories by environment, preserving order */
    const envCategoryMap = new Map<string, Set<string>>();

    for (const cell of cells) {
      modelSet.add(cell.model);
      map.set(`${cell.environment}:${cell.category}:${cell.model}`, cell);

      let catSet = envCategoryMap.get(cell.environment);
      if (!catSet) {
        catSet = new Set<string>();
        envCategoryMap.set(cell.environment, catSet);
      }
      catSet.add(cell.category);
    }

    /** Build environment groups, sorting categories by ascending avg pass rate */
    const groups: EnvironmentGroup[] = [];
    for (const [environment, catSet] of envCategoryMap.entries()) {
      const categoryList = Array.from(catSet);
      categoryList.sort((catA, catB) => {
        const cellsA = cells.filter(
          (cell) => cell.environment === environment && cell.category === catA,
        );
        const cellsB = cells.filter(
          (cell) => cell.environment === environment && cell.category === catB,
        );
        const avgA =
          cellsA.reduce((sum, cell) => sum + cell.passRate, 0) /
          (cellsA.length || 1);
        const avgB =
          cellsB.reduce((sum, cell) => sum + cell.passRate, 0) /
          (cellsB.length || 1);
        return avgA - avgB;
      });
      groups.push({ environment, categories: categoryList });
    }
    groups.sort((groupA, groupB) =>
      groupA.environment.localeCompare(groupB.environment),
    );

    return {
      envGroups: groups,
      models: Array.from(modelSet).sort(),
      cellMap: map,
    };
  }, [cells]);

  /** Pre-compute stable SVG layout positions without render-time mutation */
  const svgWidth = LABEL_W + models.length * (CELL_W + GAP);
  const totalCategoryRows = envGroups.reduce(
    (sum, group) => sum + group.categories.length,
    0,
  );
  const svgHeight =
    LABEL_H +
    totalCategoryRows * (CELL_H + GAP) +
    envGroups.length * ENV_HEADER_H +
    Math.max(0, envGroups.length - 1) * ENV_GAP;

  const envLayouts = useMemo<EnvironmentLayout[]>(
    () =>
      envGroups.map((group, groupIndex) => {
        const sectionY = envGroups
          .slice(0, groupIndex)
          .reduce(
            (sum, prior) =>
              sum +
              ENV_HEADER_H +
              prior.categories.length * (CELL_H + GAP) +
              ENV_GAP,
            LABEL_H,
          );
        return {
          environment: group.environment,
          sectionY,
          categories: group.categories.map((category, categoryIndex) => ({
            category,
            cellY: sectionY + ENV_HEADER_H + categoryIndex * (CELL_H + GAP),
          })),
        };
      }),
    [envGroups],
  );

  if (cells.length === 0) {
    return (
      <div className="flex items-center justify-center py-10 text-[13px] text-envoi-text-dim">
        No difficulty data available
      </div>
    );
  }

  /** Navigate to trajectory list filtered by environment and model */
  function handleCellClick(environment: string, model: string) {
    const params = new URLSearchParams();
    params.set("environment", environment);
    params.set("model", model);
    router.push(
      `/project/${encodeURIComponent(project)}/trajectory?${params.toString()}`,
    );
  }

  return (
    <TooltipProvider>
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="w-full"
        style={{ maxWidth: svgWidth }}
      >
        {/* Model labels (top) */}
        {models.map((model, modelIndex) => {
          const label = splitModelLabel(model);
          const x = LABEL_W + modelIndex * (CELL_W + GAP) + CELL_W / 2;

          return (
            <text
              key={`model-${model}`}
              x={x}
              y={14}
              textAnchor="middle"
              style={{ fontSize: "12px", fill: T.textDim, fontWeight: 500 }}
            >
              <tspan x={x} dy={0}>
                {label.agent}
              </tspan>
              {label.model && (
                <tspan x={x} dy="1.2em">
                  {label.model}
                </tspan>
              )}
            </text>
          );
        })}

        {/* Environment sections */}
        {envLayouts.map((group) => {
          const categoryElements = group.categories.map(
            ({ category, cellY }) => {
              return (
                <g key={`row-${group.environment}-${category}`}>
                  {/* Category label */}
                  <text
                    x={LABEL_W - 10}
                    y={cellY + CELL_H / 2 + 4}
                    textAnchor="end"
                    style={{ fontSize: "12px", fill: T.textMuted }}
                  >
                    {category}
                  </text>

                  {/* Cells for this category */}
                  {models.map((model, modelIndex) => {
                    const cell = cellMap.get(
                      `${group.environment}:${category}:${model}`,
                    );
                    const rate = cell?.passRate ?? 0;
                    const cellX = LABEL_W + modelIndex * (CELL_W + GAP);
                    const frontier = isFrontier(rate);

                    return (
                      <Tooltip
                        key={`${group.environment}:${category}:${model}`}
                      >
                        <TooltipTrigger asChild>
                          <g
                            className="cursor-pointer"
                            onClick={() =>
                              handleCellClick(group.environment, model)
                            }
                          >
                            <rect
                              x={cellX}
                              y={cellY}
                              width={CELL_W}
                              height={CELL_H}
                              rx={3}
                              fill={passRateColor(rate)}
                            />
                            {/* Frontier band indicator — dashed border */}
                            {frontier && (
                              <rect
                                x={cellX + 1}
                                y={cellY + 1}
                                width={CELL_W - 2}
                                height={CELL_H - 2}
                                rx={2}
                                fill="none"
                                stroke="#ffffff"
                                strokeWidth={1.5}
                                strokeDasharray="4 2"
                                opacity={0.8}
                              />
                            )}
                            <text
                              x={cellX + CELL_W / 2}
                              y={cellY + CELL_H / 2 + 4}
                              textAnchor="middle"
                              style={{
                                fontSize: "13px",
                                fill: textColor(rate),
                                fontWeight: 600,
                              }}
                            >
                              {(rate * 100).toFixed(0)}%
                            </text>
                          </g>
                        </TooltipTrigger>
                        <TooltipContent>
                          <span className="font-semibold">{model}</span> on{" "}
                          {group.environment}/{category}: best{" "}
                          {(rate * 100).toFixed(1)}%
                          {cell ? ` (${cell.attempts} trajectories)` : ""}
                          {frontier ? " — frontier range" : ""}
                        </TooltipContent>
                      </Tooltip>
                    );
                  })}
                </g>
              );
            },
          );

          return (
            <g key={`env-${group.environment}`}>
              {/* Environment section header */}
              <text
                x={0}
                y={group.sectionY + ENV_HEADER_H / 2 + 5}
                style={{
                  fontSize: "13px",
                  fill: T.text,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                }}
              >
                {group.environment}
              </text>
              {/* Divider line under header */}
              <line
                x1={0}
                y1={group.sectionY + ENV_HEADER_H - 2}
                x2={svgWidth}
                y2={group.sectionY + ENV_HEADER_H - 2}
                stroke={T.borderLight}
                strokeWidth={1}
              />
              {categoryElements}
            </g>
          );
        })}
      </svg>

      {/* Legend */}
      <div className="mt-3 flex items-center gap-4 text-[11px] text-envoi-text-dim">
        <span>Click a cell to view trajectories</span>
        <span className="flex items-center gap-1.5">
          <svg width="16" height="12">
            <rect
              x={0}
              y={0}
              width={16}
              height={12}
              rx={2}
              fill={passRateColor(0.5)}
            />
            <rect
              x={1}
              y={1}
              width={14}
              height={10}
              rx={1}
              fill="none"
              stroke="#ffffff"
              strokeWidth={1}
              strokeDasharray="3 2"
              opacity={0.8}
            />
          </svg>
          Frontier band (35-65%)
        </span>
      </div>
    </TooltipProvider>
  );
}
