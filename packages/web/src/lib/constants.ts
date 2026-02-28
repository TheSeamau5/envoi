/**
 * Application constants: suites, milestones, models, grouping dimensions.
 * Shared across server and client components.
 *
 * Hardcoded values serve as defaults/fallbacks for the C compiler environment.
 * Real data provides dynamic suite definitions via the Trajectory.suites field.
 */

import type { Suite, MilestoneDef } from "./types";

/** Default test suites (C compiler environment fallback) */
export const SUITES: Suite[] = [
  { name: "basics", total: 35 },
  { name: "wacct", total: 1559 },
  { name: "c_testsuite", total: 220 },
  { name: "torture", total: 370 },
];

/** Compute total tests from a suite array */
export function computeTotalTests(suites: Suite[]): number {
  return suites.reduce((sum, suite) => sum + suite.total, 0);
}

/** Total number of tests across all default suites */
export const TOTAL_TESTS = computeTotalTests(SUITES);

/** Compute milestones dynamically from suite definitions */
export function computeMilestones(suites: Suite[]): MilestoneDef[] {
  const total = computeTotalTests(suites);
  const milestones: MilestoneDef[] = [
    { id: "o25",  label: ">25%", suite: undefined, threshold: Math.ceil(total * 0.25), group: "overall" },
    { id: "o50",  label: ">50%", suite: undefined, threshold: Math.ceil(total * 0.50), group: "overall" },
    { id: "o90",  label: ">90%", suite: undefined, threshold: Math.ceil(total * 0.90), group: "overall" },
    { id: "o100", label: "100%", suite: undefined, threshold: total,                   group: "overall" },
  ];
  for (const suite of suites) {
    const prefix = suite.name[0];
    milestones.push(
      { id: `${prefix}25`,  label: ">25%", suite: suite.name, threshold: Math.ceil(suite.total * 0.25), group: suite.name },
      { id: `${prefix}50`,  label: ">50%", suite: suite.name, threshold: Math.ceil(suite.total * 0.50), group: suite.name },
      { id: `${prefix}90`,  label: ">90%", suite: suite.name, threshold: Math.ceil(suite.total * 0.90), group: suite.name },
      { id: `${prefix}100`, label: "100%", suite: suite.name, threshold: suite.total,                   group: suite.name },
    );
  }
  return milestones;
}

/** Available model identifiers in agent/model format (fallback) */
export const MODELS = [
  "claude-code/opus-4.6",
  "claude-code/sonnet-4.6",
  "codex/gpt-5.3-codex",
  "opencode/glm-5",
  "opencode/minimax-m2.5",
] as const;

/** Default milestones computed from default suites */
export const MILESTONES: MilestoneDef[] = computeMilestones(SUITES);

/** Dimensions available for grouping trajectories in Setup Compare mode */
export const GROUPABLE_DIMENSIONS = [
  { key: "model", label: "Model" },
  { key: "implLang", label: "Programming Language" },
  { key: "lang", label: "Natural Language" },
  { key: "target", label: "Target" },
  { key: "milestone", label: "Milestone" },
  { key: "agent", label: "Agent" },
  { key: "sandbox", label: "Sandbox" },
] as const;

/** Maximum duration in minutes for chart X axis */
export const MAX_DURATION = 480;
