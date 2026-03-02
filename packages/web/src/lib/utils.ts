/**
 * Shared utility functions: formatting, data lookups, grouping.
 * Pure functions with no React dependencies.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Trajectory, TrajectoryGroup, MilestoneDef, Commit } from "./types";

/** Merge Tailwind classes with deduplication */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format minutes into human-readable duration (e.g., 125 -> "2 hrs 5 min") */
export function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (!hours) {
    return `${mins} min`;
  }
  if (!mins) {
    return `${hours} hr${hours > 1 ? "s" : ""}`;
  }
  return `${hours} hr${hours > 1 ? "s" : ""} ${mins} min`;
}

/** Calculate median of a numeric array */
export function median(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const sorted = [...values].sort((first, second) => first - second);
  const mid = Math.floor(sorted.length / 2);
  const midVal = sorted[mid];
  if (midVal === undefined) {
    return 0;
  }
  if (sorted.length % 2 !== 0) {
    return midVal;
  }
  const prevVal = sorted[mid - 1];
  return prevVal === undefined ? midVal : (prevVal + midVal) / 2;
}

/** Find the first commit where a milestone threshold is reached */
export function findMilestone(trace: Trajectory, milestone: MilestoneDef): Commit | undefined {
  for (const commit of trace.commits) {
    const value = milestone.suite
      ? (commit.suiteState[milestone.suite] ?? 0)
      : commit.totalPassed;
    if (value >= milestone.threshold) {
      return commit;
    }
  }
  return undefined;
}

/** Extract a parameter value from a trajectory by dimension key */
export function getTrajectoryParam(trace: Trajectory, dimensionKey: string): string {
  if (dimensionKey === "model") {
    return trace.model;
  }
  if (dimensionKey === "environment") {
    return trace.environment || "unknown";
  }
  const value = Object.entries(trace.params).find(([key]) => key === dimensionKey)?.[1];
  return value ?? "\u2014";
}

/** Group traces by one or more dimension keys, returning sorted groups */
export function groupTraces(traces: Trajectory[], dimensions: string[]): TrajectoryGroup[] {
  if (dimensions.length === 0) {
    return [{ key: "all", label: "All Traces", traces }];
  }

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

/** Format an ISO date string as YY/MM/DD HH:MM:SS */
export function formatDate(isoString: string): string {
  const date = new Date(isoString);
  const yy = String(date.getFullYear()).slice(2);
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const min = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${yy}/${mm}/${dd} ${hh}:${min}:${ss}`;
}

/** Format an ISO date string as a short date + time (e.g., "Jan 15, 10:00") */
export function formatDateTime(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// ---------------------------------------------------------------------------
// Chart axis helpers — used by progress-curves, suite-breakdown, setup-compare
// ---------------------------------------------------------------------------

/** Compute the max duration across traces (raw max, no padding) */
export function computeMaxDuration(traces: Trajectory[]): number {
  let max = 0;
  for (const trace of traces) {
    const lastCommit = trace.commits[trace.commits.length - 1];
    if (lastCommit && lastCommit.minutesElapsed > max) {
      max = lastCommit.minutesElapsed;
    }
  }
  return max <= 0 ? 60 : max;
}

/**
 * Pick a "nice" tick interval that yields ~4-8 ticks for the given range.
 * Scales from seconds to weeks without any hardcoded ceiling.
 */
function niceTickInterval(range: number): number {
  if (range <= 0) {
    return 1;
  }
  // Target ~5 ticks
  const rough = range / 5;
  // "Nice" multipliers: 1, 2, 5 (then 10, 20, 50, etc.)
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const residual = rough / magnitude;
  let nice: number;
  if (residual <= 1.5) {
    nice = 1;
  }
  else if (residual <= 3.5) {
    nice = 2;
  }
  else if (residual <= 7.5) {
    nice = 5;
  }
  else {
    nice = 10;
  }
  return nice * magnitude;
}

/** Generate X-axis tick values for a given max duration in minutes */
export function getXTicks(maxDuration: number): number[] {
  if (maxDuration <= 0) {
    return [0];
  }
  const interval = niceTickInterval(maxDuration);
  const ticks: number[] = [];
  for (let tick = 0; tick <= maxDuration; tick += interval) {
    ticks.push(Math.round(tick));
  }
  // Add final tick if there's remaining space
  const lastTick = ticks[ticks.length - 1];
  if (lastTick !== undefined && maxDuration - lastTick > interval * 0.3) {
    ticks.push(Math.ceil(maxDuration / interval) * interval);
  }
  return ticks;
}

/** Format a minute value as a concise label (e.g., "5m", "2h", "3d", "2w") */
export function formatXTick(minutes: number): string {
  if (minutes === 0) {
    return "0";
  }
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }
  if (minutes < 1440) {
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return mins === 0 ? `${hours}h` : `${hours}h${mins}m`;
  }
  const days = minutes / 1440;
  if (days < 7) {
    return `${Math.round(days)}d`;
  }
  const weeks = days / 7;
  if (Number.isInteger(Math.round(weeks))) {
    return `${Math.round(weeks)}w`;
  }
  return `${Math.round(days)}d`;
}

/** Generate Y-axis tick values for a total count (5 evenly spaced ticks) */
export function getYTicks(totalTests: number): number[] {
  return Array.from({ length: 5 }, (_, tickIdx) => Math.round((tickIdx / 4) * totalTests));
}
