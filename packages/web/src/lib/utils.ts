/**
 * Shared utility functions: formatting, data lookups, grouping.
 * Pure functions with no React dependencies.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Trajectory, TrajectoryGroup, MilestoneDef, Commit, TrajectoryParams } from "./types";

/** Merge Tailwind classes with deduplication */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format minutes into human-readable duration (e.g., 125 -> "2 hrs 5 min") */
export function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (!hours) return `${mins} min`;
  if (!mins) return `${hours} hr${hours > 1 ? "s" : ""}`;
  return `${hours} hr${hours > 1 ? "s" : ""} ${mins} min`;
}

/** Calculate median of a numeric array */
export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((first, second) => first - second);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 !== 0 ? sorted[mid]! : ((sorted[mid - 1]! + sorted[mid]!) / 2);
}

/** Find the first commit where a milestone threshold is reached */
export function findMilestone(trace: Trajectory, milestone: MilestoneDef): Commit | undefined {
  for (const commit of trace.commits) {
    const value = milestone.suite
      ? (commit.suiteState[milestone.suite] ?? 0)
      : commit.totalPassed;
    if (value >= milestone.threshold) return commit;
  }
  return undefined;
}

/** Extract a parameter value from a trajectory by dimension key */
export function getTrajectoryParam(trace: Trajectory, dimensionKey: string): string {
  if (dimensionKey === "model") return trace.model;
  const paramKey = dimensionKey as keyof TrajectoryParams;
  return trace.params[paramKey] ?? "\u2014";
}

/** Group traces by one or more dimension keys, returning sorted groups */
export function groupTraces(traces: Trajectory[], dimensions: string[]): TrajectoryGroup[] {
  if (dimensions.length === 0) return [{ key: "all", label: "All Traces", traces }];

  const map: Record<string, TrajectoryGroup> = {};
  for (const trace of traces) {
    const parts = dimensions.map((dim) => getTrajectoryParam(trace, dim));
    const key = parts.join(" \u00d7 ");
    const existing = map[key];
    if (!existing) {
      map[key] = { key, label: key, traces: [trace] };
    } else {
      existing.traces.push(trace);
    }
  }

  return Object.values(map).sort(
    (groupA, groupB) =>
      Math.max(...groupB.traces.map((trace) => trace.finalPassed)) -
      Math.max(...groupA.traces.map((trace) => trace.finalPassed))
  );
}

/** Format a number as a percentage string */
export function formatPercent(value: number, total: number): string {
  return `${((value / total) * 100).toFixed(1)}%`;
}

/** Format cost in USD */
export function formatCost(cost: number): string {
  return `$${cost.toFixed(2)}`;
}
