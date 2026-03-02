/**
 * Difficulty heatmap — SVG color matrix showing pass rates per (category, model),
 * segmented by environment with section headers.
 * Client component for hover interactions via shadcn Tooltip.
 */

"use client";

import { useMemo } from "react";
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
};

const CELL_W = 80;
const CELL_H = 28;
const GAP = 2;
const LABEL_W = 160;
const LABEL_H = 80;
const ENV_HEADER_H = 32;
const ENV_GAP = 16;

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

type EnvironmentGroup = {
  environment: string;
  categories: string[];
};

export function DifficultyHeatmap({ cells }: DifficultyHeatmapProps) {
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
        const cellsA = cells.filter((cell) => cell.environment === environment && cell.category === catA);
        const cellsB = cells.filter((cell) => cell.environment === environment && cell.category === catB);
        const avgA = cellsA.reduce((sum, cell) => sum + cell.passRate, 0) / (cellsA.length || 1);
        const avgB = cellsB.reduce((sum, cell) => sum + cell.passRate, 0) / (cellsB.length || 1);
        return avgA - avgB;
      });
      groups.push({ environment, categories: categoryList });
    }
    groups.sort((groupA, groupB) => groupA.environment.localeCompare(groupB.environment));

    return {
      envGroups: groups,
      models: Array.from(modelSet).sort(),
      cellMap: map,
    };
  }, [cells]);

  if (cells.length === 0) {
    return (
      <div className="flex items-center justify-center py-[40px] text-[11px] text-envoi-text-dim">
        No difficulty data available
      </div>
    );
  }

  /** Calculate total rows across all environments for SVG height */
  const totalCategoryRows = envGroups.reduce((sum, group) => sum + group.categories.length, 0);
  const svgWidth = LABEL_W + models.length * (CELL_W + GAP);
  const svgHeight = LABEL_H
    + totalCategoryRows * (CELL_H + GAP)
    + envGroups.length * ENV_HEADER_H
    + (envGroups.length - 1) * ENV_GAP;

  let currentY = LABEL_H;

  return (
    <TooltipProvider>
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="w-full"
        style={{ maxWidth: svgWidth }}
      >
        {/* Model labels (top) */}
        {models.map((model, modelIndex) => (
          <text
            key={`model-${model}`}
            x={LABEL_W + modelIndex * (CELL_W + GAP) + CELL_W / 2}
            y={LABEL_H - 8}
            textAnchor="middle"
            style={{ fontSize: "9px", fill: T.textDim }}
          >
            {model.length > 12 ? `${model.slice(0, 12)}...` : model}
          </text>
        ))}

        {/* Environment sections */}
        {envGroups.map((group, groupIndex) => {
          /** Add gap between environment sections */
          if (groupIndex > 0) {
            currentY += ENV_GAP;
          }

          const sectionY = currentY;
          currentY += ENV_HEADER_H;

          const categoryElements = group.categories.map((category) => {
            const cellY = currentY;
            currentY += CELL_H + GAP;

            return (
              <g key={`row-${group.environment}-${category}`}>
                {/* Category label */}
                <text
                  x={LABEL_W - 8}
                  y={cellY + CELL_H / 2 + 3}
                  textAnchor="end"
                  style={{ fontSize: "9px", fill: T.textMuted }}
                >
                  {category}
                </text>

                {/* Cells for this category */}
                {models.map((model, modelIndex) => {
                  const cell = cellMap.get(`${group.environment}:${category}:${model}`);
                  const rate = cell?.passRate ?? 0;
                  const cellX = LABEL_W + modelIndex * (CELL_W + GAP);

                  return (
                    <Tooltip key={`${group.environment}:${category}:${model}`}>
                      <TooltipTrigger asChild>
                        <g>
                          <rect
                            x={cellX}
                            y={cellY}
                            width={CELL_W}
                            height={CELL_H}
                            rx={3}
                            fill={passRateColor(rate)}
                            className="cursor-pointer"
                          />
                          <text
                            x={cellX + CELL_W / 2}
                            y={cellY + CELL_H / 2 + 3}
                            textAnchor="middle"
                            style={{
                              fontSize: "9px",
                              fill: textColor(rate),
                              fontWeight: 600,
                            }}
                          >
                            {(rate * 100).toFixed(0)}%
                          </text>
                        </g>
                      </TooltipTrigger>
                      <TooltipContent>
                        {group.environment} / {category} × {model}: {(rate * 100).toFixed(1)}%
                        {cell ? ` (${cell.attempts} attempts)` : ""}
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </g>
            );
          });

          return (
            <g key={`env-${group.environment}`}>
              {/* Environment section header */}
              <text
                x={0}
                y={sectionY + ENV_HEADER_H / 2 + 4}
                style={{
                  fontSize: "10px",
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
                y1={sectionY + ENV_HEADER_H - 2}
                x2={svgWidth}
                y2={sectionY + ENV_HEADER_H - 2}
                stroke={T.borderLight}
                strokeWidth={1}
              />
              {categoryElements}
            </g>
          );
        })}
      </svg>
    </TooltipProvider>
  );
}
